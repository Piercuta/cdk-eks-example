from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_eks as eks
from aws_cdk import aws_iam as iam
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from constructs import Construct
from aws_cdk.lambda_layer_kubectl_v32 import KubectlV32Layer
from aws_cdk import Duration
import json


class EksFargateClusterStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. VPC
        vpc = ec2.Vpc(self, "FastApiFargateVpc", max_azs=2)

        # 2. Fargate Cluster EKS
        cluster = eks.FargateCluster(
            self, "FastApiEksFargateCluster",
            # cluster_name="fastapi-eks-fargate-cluster",
            version=eks.KubernetesVersion.V1_32,
            vpc=vpc,
            kubectl_layer=KubectlV32Layer(self, "KubectlLayer"),
            cluster_logging=[
                eks.ClusterLoggingTypes.API,
                eks.ClusterLoggingTypes.AUDIT,
                eks.ClusterLoggingTypes.AUTHENTICATOR,
                eks.ClusterLoggingTypes.CONTROLLER_MANAGER,
                eks.ClusterLoggingTypes.SCHEDULER
            ]
        )

        cluster.aws_auth.add_role_mapping(
            iam.Role.from_role_arn(
                self, "SSOAdminRole",
                "arn:aws:iam::532673134317:role/AWSReservedSSO_AdministratorAccess_ecdb820f0c77380d"
            ),
            groups=["system:masters"],
            username="pcourteille"
        )

        # 3. Namespace
        fastapi_ns = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": "fastapi",
                "labels": {
                    "name": "fastapi"
                }
            }
        }
        fastapi_namespace = cluster.add_manifest("FastApiNamespace", fastapi_ns)

        cloudwatch_ns = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": "amazon-cloudwatch"
            }
        }
        cloudwatch_namespace = cluster.add_manifest("CloudWatchNamespace", cloudwatch_ns)

        # 4. Fargate Profile
        fastapi_profile = cluster.add_fargate_profile(
            "AppProfile",
            fargate_profile_name="AppProfile",
            selectors=[
                eks.Selector(namespace="fastapi"),
            ]
        )

        # fastapi_profile.node.add_dependency(fastapi_namespace)

        cloudwatch_profile = cluster.add_fargate_profile(
            "MonitoringProfile",
            fargate_profile_name="MonitoringProfile",
            selectors=[
                eks.Selector(namespace="amazon-cloudwatch"),
            ]
        )

        # cloudwatch_profile.node.add_dependency(cloudwatch_namespace)

        # 5. CloudWatch Agent
        cloudwatch_sa = cluster.add_service_account(
            "CloudWatchAgentSA",
            name="cloudwatch-agent",
            namespace="amazon-cloudwatch"
        )

        cloudwatch_policy_doc = iam.PolicyDocument.from_json(
            json.load(open("policy/cloudwatch-logs-policy.json"))
        )

        cloudwatch_sa.role.attach_inline_policy(
            iam.Policy(self, "CloudWatchPolicy", document=cloudwatch_policy_doc)
        )

        cloudwatch_sa.node.add_dependency(cloudwatch_namespace)

        cloudwatch_chart = cluster.add_helm_chart(
            "CloudWatchAgentChart",
            chart="aws-cloudwatch-metrics",
            release="cloudwatch-agent",
            repository="https://aws.github.io/eks-charts",
            namespace="amazon-cloudwatch",
            values={
                "serviceAccount": {
                    "create": False,
                    "name": "cloudwatch-agent"
                },
                "clusterName": cluster.cluster_name,
                "region": self.region
            }
        )

        cloudwatch_chart.node.add_dependency(cloudwatch_sa)

        # 6. Metrics Server
        metrics_server_chart = cluster.add_helm_chart(
            "MetricsServer",
            chart="metrics-server",
            release="metrics-server",
            repository="https://kubernetes-sigs.github.io/metrics-server/",
            namespace="kube-system",
            values={
                "args": [
                    "--kubelet-insecure-tls",  # souvent nécessaire sur EKS
                    "--kubelet-preferred-address-types=InternalIP"
                ]
            }
        )

        metrics_server_chart.node.add_dependency(cloudwatch_chart)

        # 8. AWS Load Balancer Controller
        alb_sa = cluster.add_service_account(
            "ALBControllerSA",
            name="aws-load-balancer-controller",
            namespace="kube-system"
        )

        # Attacher la policy à ce ServiceAccount
        alb_policy = iam.PolicyDocument.from_json(
            json.load(open("policy/alb-controller-policy.json"))
        )

        alb_sa.role.attach_inline_policy(
            iam.Policy(self, "ALBControllerIAMPolicy", document=alb_policy)
        )

        alb_sa.node.add_dependency(metrics_server_chart)

        alb_chart = cluster.add_helm_chart(
            "AWSLoadBalancerController",
            chart="aws-load-balancer-controller",
            repository="https://aws.github.io/eks-charts",
            namespace="kube-system",
            release="alb-controller",
            version="1.7.1",  # ou dernière version stable
            values={
                "clusterName": cluster.cluster_name,
                "serviceAccount": {
                    "create": False,
                    "name": "aws-load-balancer-controller"
                },
                "region": self.region,
                "vpcId": vpc.vpc_id,
                "replicaCount": 2
            }
        )

        alb_chart.node.add_dependency(alb_sa)

        self.eks_cluster = cluster
        self.alb_chart = alb_chart
        self.vpc = vpc
