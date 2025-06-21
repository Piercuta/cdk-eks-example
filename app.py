#!/usr/bin/env python3
import os

import aws_cdk as cdk

from my_fastapi_eks.eks_cluster_stack import EksClusterStack
from my_fastapi_eks.eks_fast_api_service_stack import EksFastApiServiceStack

app = cdk.App()
eks_cluster_stack = EksClusterStack(
    app,
    "EksClusterStack",
    stack_name="EksClusterStack",
    tags={
        "project": "fastapi-eks",
        "env": "dev",
        "owner": "pcourteille"
    },
    env=cdk.Environment(account="532673134317", region="eu-west-1"),
)

eks_service_stack = EksFastApiServiceStack(
    app,
    "EksFastApiServiceStack",
    stack_name="EksFastApiServiceStack",
    cluster=eks_cluster_stack.eks_cluster,
    alb_chart=eks_cluster_stack.alb_chart,
    tags={
        "project": "fastapi-eks",
        "env": "dev",
        "owner": "pcourteille"
    },
    env=cdk.Environment(account="532673134317", region="eu-west-1"),
)

app.synth()
