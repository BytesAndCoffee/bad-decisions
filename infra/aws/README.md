# AWS hybrid deployment scaffold

This directory is an AWS CDK scaffold for the hybrid deployment shape:

```text
existing nginx/systemd edge -> ALB -> ECS/Fargate service -> Bad Decisions API
                                      |
                                      +-> ECR image
                                      +-> CloudWatch Logs
```

The existing VPS remains a valid management and compatibility edge. The ALB
is the cloud-native entry point for horizontally scaled API tasks. This stack
does not create DNS records, certificates, Garage storage, or a production
deployment automatically.

## Prerequisites

- AWS CDK v2 and Python 3.12+
- An ECR image containing the Bad Decisions server
- AWS credentials for the target account and region

Install the CDK dependencies in a dedicated virtual environment, then run:

```bash
cd infra/aws
python -m pip install -r requirements.txt
cdk synth --strict
cdk diff
```

Set `BAD_DECISIONS_IMAGE` to an ECR image URI before synthesis. The default is
the repository's `latest` tag in the stack-created repository and is intended
only as a placeholder for the first bootstrap. Set `BAD_DECISIONS_DESIRED_COUNT`
to scale the service, and `BAD_DECISIONS_VPC_ID` only if the service must use an
existing VPC. The default creates a new VPC with public and private subnets.

Do not use `cdk deploy --hotswap` or `--express` for production. Review
`cdk synth --strict` and `cdk diff` before any deployment.

The application container must listen on port 8000 and expose `/healthz`.
Those values are configurable in the stack source if the image differs.
