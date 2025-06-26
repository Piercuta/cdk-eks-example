#!/usr/bin/env python3
import aws_cdk as cdk

from my_fastapi_eks.karpenter.cdk_eks_karpenter_stack import CdkEksKarpenterStack
from my_fastapi_eks.karpenter.k8s_deploy_pipeline_stack import K8sDeployPipelineStack

app = cdk.App()

k8s_deploy_pipeline_stack = K8sDeployPipelineStack(
    app,
    "K8sDeployPipelineStack",
    stack_name="K8sDeployPipelineStack",
)

eks_karpenter_stack = CdkEksKarpenterStack(
    app,
    "CdkEksKarpenterStack",
    stack_name="CdkEksKarpenterStack",
    codebuild_project=k8s_deploy_pipeline_stack.codebuild_project,
)

app.synth()
