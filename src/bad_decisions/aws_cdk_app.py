from __future__ import annotations

from .errors import MissingExtraError

try:
    import aws_cdk as cdk
except ImportError as exc:  # optional dependency
    raise MissingExtraError("aws-deploy") from exc

from .aws_stack import BadDecisionsAwsStack


def main() -> None:
    app = cdk.App()
    BadDecisionsAwsStack(app, "BadDecisionsHybrid")
    app.synth()


if __name__ == "__main__":
    main()
