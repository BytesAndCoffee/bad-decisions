from __future__ import annotations

import aws_cdk as cdk

from .aws_stack import BadDecisionsAwsStack


def main() -> None:
    app = cdk.App()
    BadDecisionsAwsStack(app, "BadDecisionsHybrid")
    app.synth()


if __name__ == "__main__":
    main()
