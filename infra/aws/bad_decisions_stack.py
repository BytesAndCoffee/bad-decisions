"""Cloud-native compute plane for the Bad Decisions hybrid deployment."""

from __future__ import annotations

import os

from aws_cdk import (
    Aws,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_logs as logs,
)
from constructs import Construct


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be at least 1")
    return parsed


class BadDecisionsStack(Stack):
    """ECR + ECS/Fargate + ALB compute plane.

    Stateful card-pack storage and the existing Garage/object archive remain
    outside this stack. The task is deliberately immutable at runtime: packs
    are supplied by the image or an external read-only object source.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)

        vpc_id = os.getenv("BAD_DECISIONS_VPC_ID")
        if vpc_id:
            vpc = ec2.Vpc.from_lookup(self, "ExistingVpc", vpc_id=vpc_id)
        else:
            vpc = ec2.Vpc(
                self,
                "Vpc",
                max_azs=2,
                nat_gateways=1,
                restrict_default_security_group=True,
            )

        repository_name = os.getenv("BAD_DECISIONS_REPOSITORY_NAME")
        repository = (
            ecr.Repository.from_repository_name(self, "Repository", repository_name)
            if repository_name
            else ecr.Repository(
                self,
                "Repository",
                repository_name="bad-decisions",
                image_scan_on_push=True,
                image_tag_mutability=ecr.TagMutability.IMMUTABLE,
                removal_policy=RemovalPolicy.RETAIN,
            )
        )
        image_uri = os.getenv("BAD_DECISIONS_IMAGE")
        if image_uri and repository_name:
            image_tag = image_uri.rsplit(":", 1)[-1]
            image = ecs.ContainerImage.from_ecr_repository(repository, image_tag)
        elif image_uri:
            image = ecs.ContainerImage.from_registry(image_uri)
        else:
            image = ecs.ContainerImage.from_ecr_repository(repository, "latest")

        cluster = ecs.Cluster(self, "Cluster", vpc=vpc, container_insights=True)
        logs_group = logs.LogGroup(
            self,
            "Logs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.RETAIN,
        )
        task = ecs.FargateTaskDefinition(
            self,
            "TaskDefinition",
            cpu=512,
            memory_limit_mib=1024,
        )
        container = task.add_container(
            "Api",
            image=image,
            logging=ecs.LogDrivers.aws_logs(stream_prefix="api", log_group=logs_group),
            environment={"PORT": "8000", "BIND_HOST": "0.0.0.0"},
            readonly_root_filesystem=True,
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -fsS http://127.0.0.1:8000/healthz || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(20),
            ),
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8000))

        service = ecs.FargateService(
            self,
            "Service",
            cluster=cluster,
            task_definition=task,
            desired_count=_int_env("BAD_DECISIONS_DESIRED_COUNT", 2),
            assign_public_ip=False,
            health_check_grace_period=Duration.seconds(60),
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
        )
        alb = elbv2.ApplicationLoadBalancer(self, "LoadBalancer", vpc=vpc, internet_facing=True)
        listener = alb.add_listener("HttpListener", port=80, open=True)
        listener.add_targets(
            "ApiTargets",
            port=8000,
            targets=[service],
            health_check=elbv2.HealthCheck(path="/healthz", healthy_http_codes="200"),
        )

        CfnOutput(self, "LoadBalancerDnsName", value=alb.load_balancer_dns_name)
        CfnOutput(self, "EcrRepositoryUri", value=repository.repository_uri)
        CfnOutput(self, "ServiceName", value=service.service_name)
        CfnOutput(self, "Region", value=Aws.REGION)
