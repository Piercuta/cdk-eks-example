from aws_cdk import (
    Stack,
    aws_codebuild as codebuild,
    aws_s3_assets as assets,
    aws_s3 as s3,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as cp_actions,
    aws_iam as iam,
)
from constructs import Construct


class K8sDeployPipelineStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 🚀 Créer un asset depuis un dossier local
        source_asset = assets.Asset(
            self, "SourceAsset",
            path="./my_fastapi_eks/karpenter/deploy_assets"  # 👈 Ton dossier local avec buildspec et manifests
        )

        # 🔧 CodeBuild project
        project = codebuild.PipelineProject(
            self, "BuildProject",
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0
            ),
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec-k8s-deploy.yaml"),
            environment_variables={
                "EKS_CLUSTER_NAME": codebuild.BuildEnvironmentVariable(value="karpenter-eks-cluster"),
                "FASTAPI_IMAGE": codebuild.BuildEnvironmentVariable(value="532673134317.dkr.ecr.eu-west-1.amazonaws.com/services/eks/fastapi_hello_world:latest"),
                "CERTIFICATE_ARN": codebuild.BuildEnvironmentVariable(value="arn:aws:acm:eu-west-1:532673134317:certificate/905d0d16-87e8-4e89-a88c-b6053f472e81"),
                "DOMAIN": codebuild.BuildEnvironmentVariable(value="fastapi-karpenter.piercuta.com"),
            }
        )

        project.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "eks:*",
                    "ec2:*"
                ],
                resources=["*"]
            )
        )

        # 🔗 Pipeline Artifact
        source_output = codepipeline.Artifact()

        # 🚀 CodePipeline
        pipeline = codepipeline.Pipeline(self, "Pipeline")

        # 🌟 Source : Asset S3
        pipeline.add_stage(
            stage_name="Source",
            actions=[
                cp_actions.S3SourceAction(
                    action_name="S3Source",
                    bucket=source_asset.bucket,
                    bucket_key=source_asset.s3_object_key,
                    output=source_output
                )
            ]
        )

        # 🔥 Build stage
        pipeline.add_stage(
            stage_name="Build",
            actions=[
                cp_actions.CodeBuildAction(
                    action_name="BuildAction",
                    project=project,
                    input=source_output
                )
            ]
        )

        # ✅ Donner le droit à CodePipeline et CodeBuild de lire l'asset
        source_asset.bucket.grant_read(project.role)

        self.codebuild_project = project
