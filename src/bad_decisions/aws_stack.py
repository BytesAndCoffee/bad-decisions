from __future__ import annotations

import os
from pathlib import Path

from aws_cdk import (
    Aws, CfnOutput, Duration, RemovalPolicy, Stack,
    aws_certificatemanager as acm, aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins, aws_cloudwatch as cloudwatch,
    aws_dynamodb as dynamodb, aws_ec2 as ec2, aws_ecr as ecr,
    aws_ecs as ecs, aws_elasticloadbalancingv2 as elbv2,
    aws_lambda as lambda_, aws_lambda_event_sources as lambda_events,
    aws_logs as logs, aws_route53 as route53, aws_route53_targets as route53_targets,
    aws_s3 as s3, aws_secretsmanager as secretsmanager, aws_sqs as sqs,
)
from constructs import Construct


def _integer(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


class BadDecisionsAwsStack(Stack):
    """Complete AWS deployment; the Linux/systemd deployment remains independent."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)
        vpc = ec2.Vpc(
            self, "Vpc", max_azs=2, nat_gateways=0, restrict_default_security_group=True,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC),
                ec2.SubnetConfiguration(name="compute", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            ],
        )
        vpc.add_gateway_endpoint("S3Endpoint", service=ec2.GatewayVpcEndpointAwsService.S3)
        vpc.add_gateway_endpoint("DynamoEndpoint", service=ec2.GatewayVpcEndpointAwsService.DYNAMODB)
        endpoint_subnets = ec2.SubnetSelection(subnets=[vpc.isolated_subnets[0]])
        for identifier, service in (
            ("EcrApi", ec2.InterfaceVpcEndpointAwsService.ECR),
            ("EcrDocker", ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER),
            ("Logs", ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS),
            ("Secrets", ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER),
        ):
            vpc.add_interface_endpoint(identifier, service=service, private_dns_enabled=True, subnets=endpoint_subnets)

        bucket = s3.Bucket(
            self, "PackArchive", block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED, enforce_ssl=True, versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        archive_origin = origins.S3BucketOrigin.with_origin_access_control(bucket)
        catalog_cache = cloudfront.CachePolicy(
            self, "CatalogCache", default_ttl=Duration.seconds(30),
            min_ttl=Duration.seconds(0), max_ttl=Duration.minutes(5),
        )
        public_paths_only = cloudfront.Function(
            self, "PublicPackPathsOnly",
            code=cloudfront.FunctionCode.from_inline(
                "function handler(event){var p=event.request.uri;"
                "if(p==='/packs'||p.indexOf('/packs/')===0){return event.request;}"
                "return {statusCode:404,statusDescription:'Not Found'};}"
            ),
        )
        public_guard = [cloudfront.FunctionAssociation(
            function=public_paths_only, event_type=cloudfront.FunctionEventType.VIEWER_REQUEST
        )]
        distribution = cloudfront.Distribution(
            self, "PackDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=archive_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                function_associations=public_guard,
            ),
            additional_behaviors={
                "packs/index": cloudfront.BehaviorOptions(
                    origin=archive_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                    cache_policy=catalog_cache,
                    function_associations=public_guard,
                )
            },
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
        )
        index_dlq = sqs.Queue(
            self, "CatalogIndexerDlq", encryption=sqs.QueueEncryption.SQS_MANAGED,
            retention_period=Duration.days(14),
        )
        indexer = lambda_.Function(
            self, "CatalogIndexer", runtime=lambda_.Runtime.PYTHON_3_12,
            handler="catalog_index.handler",
            code=lambda_.Code.from_asset(str(Path(__file__).with_name("aws_lambda"))),
            timeout=Duration.seconds(30), memory_size=256,
            environment={"BUCKET": bucket.bucket_name}, dead_letter_queue=index_dlq,
            retry_attempts=2, log_retention=logs.RetentionDays.ONE_MONTH,
        )
        bucket.grant_read(indexer, "catalog/*")
        bucket.grant_put(indexer, "packs/index")
        indexer.add_event_source(lambda_events.S3EventSource(
            bucket, events=[s3.EventType.OBJECT_CREATED, s3.EventType.OBJECT_REMOVED],
            filters=[s3.NotificationKeyFilter(prefix="catalog/", suffix=".json")],
        ))
        cloudwatch.Alarm(self, "CatalogIndexerErrors", metric=indexer.metric_errors(), threshold=1, evaluation_periods=1)

        consequences = dynamodb.Table(
            self, "Consequences", partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="expires_at", point_in_time_recovery=True,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,
        )

        repository_name = os.getenv("BAD_DECISIONS_REPOSITORY_NAME", "bad-decisions")
        repository = ecr.Repository.from_repository_name(self, "Repository", repository_name)
        image_tag = os.environ["BAD_DECISIONS_IMAGE"].rsplit(":", 1)[-1]
        management_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "ManagementSecret", os.environ["BAD_DECISIONS_AWS_SECRET_ID"]
        )
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc)
        log_group = logs.LogGroup(self, "Logs", retention=logs.RetentionDays.ONE_MONTH, removal_policy=RemovalPolicy.RETAIN)
        task = ecs.FargateTaskDefinition(self, "Task", cpu=512, memory_limit_mib=1024)
        bucket.grant_read(task.task_role, "runtime-packs/*")
        consequences.grant_read_write_data(task.task_role)
        container = task.add_container(
            "Api", image=ecs.ContainerImage.from_ecr_repository(repository, image_tag),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="api", log_group=log_group),
            environment={
                "BAD_DECISIONS_PACK_BUCKET": bucket.bucket_name,
                "BAD_DECISIONS_PACK_PREFIX": "runtime-packs/",
                "BAD_DECISIONS_CONSEQUENCES_DYNAMODB_TABLE": consequences.table_name,
            },
            secrets={"BAD_DECISIONS_MANAGEMENT_TOKEN": ecs.Secret.from_secrets_manager(management_secret)},
            readonly_root_filesystem=True,
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8000))
        service = ecs.FargateService(
            self, "Service", cluster=cluster, task_definition=task,
            desired_count=_integer("BAD_DECISIONS_DESIRED_COUNT", 1),
            assign_public_ip=False,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            health_check_grace_period=Duration.seconds(60),
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            min_healthy_percent=100, max_healthy_percent=200,
        )
        scaling = service.auto_scale_task_count(min_capacity=1, max_capacity=_integer("BAD_DECISIONS_MAX_COUNT", 2))
        scaling.scale_on_cpu_utilization(
            "CpuScaling", target_utilization_percent=55,
            scale_in_cooldown=Duration.seconds(120), scale_out_cooldown=Duration.seconds(60),
        )

        alb = elbv2.ApplicationLoadBalancer(self, "LoadBalancer", vpc=vpc, internet_facing=True)
        certificate_arn = os.getenv("BAD_DECISIONS_CERTIFICATE_ARN")
        if certificate_arn:
            domain_name = os.getenv("BAD_DECISIONS_DOMAIN_NAME")
            hosted_zone_id = os.getenv("BAD_DECISIONS_HOSTED_ZONE_ID")
            if not domain_name or not hosted_zone_id:
                raise ValueError("HTTPS requires BAD_DECISIONS_DOMAIN_NAME and BAD_DECISIONS_HOSTED_ZONE_ID")
            listener = alb.add_listener(
                "Https", port=443,
                certificates=[acm.Certificate.from_certificate_arn(self, "Certificate", certificate_arn)],
                open=True, ssl_policy=elbv2.SslPolicy.RECOMMENDED_TLS,
            )
            alb.add_redirect(source_port=80, source_protocol=elbv2.ApplicationProtocol.HTTP, target_port=443, target_protocol=elbv2.ApplicationProtocol.HTTPS)
            zone = route53.HostedZone.from_hosted_zone_attributes(
                self, "HostedZone", hosted_zone_id=hosted_zone_id,
                zone_name=os.getenv("BAD_DECISIONS_HOSTED_ZONE_NAME", domain_name),
            )
            route53.ARecord(self, "ApiAlias", zone=zone, record_name=domain_name, target=route53.RecordTarget.from_alias(route53_targets.LoadBalancerTarget(alb)))
            api_url = f"https://{domain_name}"
        elif os.getenv("BAD_DECISIONS_ALLOW_HTTP") == "1":
            listener = alb.add_listener("Http", port=80, open=True)
            api_url = f"http://{alb.load_balancer_dns_name}"
        else:
            raise ValueError("BAD_DECISIONS_CERTIFICATE_ARN is required unless BAD_DECISIONS_ALLOW_HTTP=1")
        target_group = listener.add_targets(
            "Api", port=8000, targets=[service],
            health_check=elbv2.HealthCheck(path="/healthz", healthy_http_codes="200"),
        )
        cloudwatch.Alarm(self, "UnhealthyTargets", metric=target_group.metrics.unhealthy_host_count(), threshold=1, evaluation_periods=2)
        cloudwatch.Alarm(self, "ServerErrors", metric=alb.metrics.http_code_elb(elbv2.HttpCodeElb.ELB_5XX_COUNT), threshold=5, evaluation_periods=2)

        CfnOutput(self, "LoadBalancerDnsName", value=alb.load_balancer_dns_name)
        CfnOutput(self, "PublicApiUrl", value=api_url)
        CfnOutput(self, "PackBucketName", value=bucket.bucket_name)
        CfnOutput(self, "PackDistributionDomainName", value=distribution.distribution_domain_name)
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        CfnOutput(self, "ServiceName", value=service.service_name)
        CfnOutput(self, "ConsequencesTableName", value=consequences.table_name)
        CfnOutput(self, "Region", value=Aws.REGION)
