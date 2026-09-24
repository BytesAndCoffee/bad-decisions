# AWS CDK compatibility entry point

The canonical AWS stack ships in the `bad-decisions` Python package. This
directory remains as a source-checkout compatibility entry point and imports
`bad_decisions.aws_stack.BadDecisionsAwsStack`; it contains no second stack
definition.

For normal operation, use:

```bash
bad-decisions setup aws --profile decisions --region ca-west-1 \
  --certificate-arn "$ACM_CERTIFICATE_ARN"
bad-decisions deploy aws
bad-decisions status aws
```

For a source-checkout synthesis:

```bash
cd infra/aws
python -m pip install -r requirements.txt -e ../..
cdk synth --strict
```

HTTPS is required unless `--allow-http` is explicitly selected for a disposable
smoke test. The service relies on the load balancer `/healthz` check; do not add
an in-container health command unless its executable is part of the image.
