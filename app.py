#!/usr/bin/env python3
import os

import aws_cdk as cdk

from my_fastapi_eks.eks_classic_cluster_stack import EksClassicClusterStack
from my_fastapi_eks.eks_classic_fastapi_service_stack import EksClassicFastApiServiceStack

app = cdk.App()
eks_cluster_stack = EksClassicClusterStack(
    app,
    "EksClassicClusterStack",
    stack_name="EksClassicClusterStack",
    tags={
        "project": "fastapi-eks",
        "env": "dev",
        "owner": "pcourteille"
    },
    env=cdk.Environment(account="532673134317", region="eu-west-1"),
)

eks_service_stack = EksClassicFastApiServiceStack(
    app,
    "EksClassicFastApiServiceStack",
    stack_name="EksClassicFastApiServiceStack",
    cluster=eks_cluster_stack.eks_cluster,
    alb_chart=eks_cluster_stack.alb_chart,
    metric_server=eks_cluster_stack.metrics_server,
    tags={
        "project": "fastapi-eks",
        "env": "dev",
        "owner": "pcourteille"
    },
    env=cdk.Environment(account="532673134317", region="eu-west-1"),
)

eks_service_stack.add_dependency(eks_cluster_stack)


app.synth()
