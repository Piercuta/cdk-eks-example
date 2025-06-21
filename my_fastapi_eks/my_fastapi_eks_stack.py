from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_eks as eks
from aws_cdk import aws_iam as iam
from constructs import Construct
from aws_cdk.lambda_layer_kubectl_v32 import KubectlV32Layer


class MyFastapiEksStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. VPC
        vpc = ec2.Vpc(self, "FastApiVpc", max_azs=2)

        # 2. Cluster EKS
        cluster = eks.Cluster(
            self, "FastApiEksCluster",
            version=eks.KubernetesVersion.V1_32,
            vpc=vpc,
            kubectl_layer=KubectlV32Layer(self, "KubectlLayer"),
            default_capacity=2,
            default_capacity_instance=ec2.InstanceType("t3.small")
        )

        # 3. Déploiement FastAPI depuis une image ECR
        image_uri = "532673134317.dkr.ecr.eu-west-1.amazonaws.com/services/eks/fastapi_hello_world:latest"

        app_label = {"app": "fastapi"}

        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "fastapi"},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": app_label},
                "template": {
                    "metadata": {"labels": app_label},
                    "spec": {
                        "containers": [{
                            "name": "fastapi",
                            "image": image_uri,
                            "ports": [{"containerPort": 8000}]
                        }]
                    }
                }
            }
        }

        service = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "fastapi-service"},
            "spec": {
                "selector": app_label,
                "ports": [{"port": 80, "targetPort": 8000}],
                "type": "ClusterIP"
            }
        }

        # 4. Apply les manifests
        cluster.add_manifest("FastApiDeployment", deployment)
        cluster.add_manifest("FastApiService", service)
