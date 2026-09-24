#!/usr/bin/env python3
"""CDK entry point for the Bad Decisions AWS-native deployment."""

import aws_cdk as cdk

from bad_decisions_stack import BadDecisionsStack


app = cdk.App()
BadDecisionsStack(app, "BadDecisionsHybrid")
app.synth()
