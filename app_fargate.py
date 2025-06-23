#!/usr/bin/env python3
import os

import aws_cdk as cdk

from my_fastapi_eks.fargate.eks_fargate_cluster_stack import EksFargateClusterStack
from my_fastapi_eks.fargate.eks_fargate_fastapi_service_stack import EksFargateFastApiServiceStack


app = cdk.App()

# Create the Fargate EKS cluster
fargate_cluster_stack = EksFargateClusterStack(
    app,
    "EksFargateClusterStack",
    stack_name="EksFargateClusterStac",
    tags={
        "project": "fargate-eks",
        "env": "dev",
    },
    env=cdk.Environment(account="532673134317", region="eu-west-1"),
)

# Create the FastAPI service stack that depends on the Fargate cluster
fastapi_service_stack = EksFargateFastApiServiceStack(
    app,
    "EksFargateFastApiServiceStack",
    cluster=fargate_cluster_stack.eks_cluster,
    alb_chart=fargate_cluster_stack.alb_chart,
    tags={
        "project": "fargate-eks",
        "env": "dev",
    },
    env=cdk.Environment(account="532673134317", region="eu-west-1"),
)

# Add dependency to ensure cluster is created before service
fastapi_service_stack.add_dependency(fargate_cluster_stack)

app.synth()
