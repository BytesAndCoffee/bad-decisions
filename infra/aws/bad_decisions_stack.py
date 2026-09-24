"""Compatibility import for the canonical packaged AWS stack."""

from bad_decisions.aws_stack import BadDecisionsAwsStack

BadDecisionsStack = BadDecisionsAwsStack

__all__ = ["BadDecisionsStack"]
