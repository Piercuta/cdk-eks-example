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

        # Add Fargate profiles for different namespaces
        # Default profile for kube-system namespace
        cluster.add_fargate_profile(
            "SystemProfile",
            selectors=[eks.Selector(namespace="kube-system")]
        )

        # Profile for application workloads
        cluster.add_fargate_profile(
            "AppProfile",
            selectors=[
                eks.Selector(namespace="default"),
                eks.Selector(namespace="fastapi"),
                eks.Selector(namespace="application")
            ]
        )

        # Profile for monitoring and logging
        cluster.add_fargate_profile(
            "MonitoringProfile",
            selectors=[
                eks.Selector(namespace="amazon-cloudwatch"),
                eks.Selector(namespace="monitoring"),
                eks.Selector(namespace="logging")
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

        # 3. ALB Controller for Fargate
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

        # 4. CloudWatch Agent for Fargate
        cloudwatch_ns = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": "amazon-cloudwatch"
            }
        }
        cloudwatch_namespace = cluster.add_manifest("CloudWatchNamespace", cloudwatch_ns)

        cloudwatch_sa = cluster.add_service_account(
            "CloudWatchAgentSA",
            name="cloudwatch-agent",
            namespace="amazon-cloudwatch"
        )

        cloudwatch_sa.node.add_dependency(cloudwatch_namespace)

        cloudwatch_policy_doc = iam.PolicyDocument.from_json(
            json.load(open("policy/cloudwatch-logs-policy.json"))
        )

        cloudwatch_sa.role.attach_inline_policy(
            iam.Policy(self, "CloudWatchPolicy", document=cloudwatch_policy_doc)
        )

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

        cloudwatch_chart.node.add_dependency(alb_chart)
        cloudwatch_chart.node.add_dependency(cloudwatch_namespace)

        # 5. Metrics Server for Fargate
        cluster.add_helm_chart(
            "MetricsServer",
            chart="metrics-server",
            repository="https://kubernetes-sigs.github.io/metrics-server/",
            namespace="kube-system",
            values={
                "args": [
                    "--kubelet-insecure-tls",  # souvent nécessaire sur EKS
                    "--kubelet-preferred-address-types=InternalIP"
                ]
            }
        )

        # 6. CoreDNS for Fargate (ensures DNS resolution works properly)
        cluster.add_helm_chart(
            "CoreDNS",
            chart="coredns",
            repository="https://coredns.github.io/helm",
            namespace="kube-system",
            release="coredns",
            values={
                "replicaCount": 2,
                "resources": {
                    "requests": {
                        "cpu": "100m",
                        "memory": "128Mi"
                    },
                    "limits": {
                        "cpu": "200m",
                        "memory": "256Mi"
                    }
                }
            }
        )

        # 7. AWS Load Balancer Controller for Fargate (additional configuration)
        # Note: Fargate requires specific annotations for ALB ingress
        cluster.add_manifest("FargateALBAnnotations", {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "fargate-alb-config",
                "namespace": "kube-system"
            },
            "data": {
                "fargate-profile-enabled": "true"
            }
        })

        self.eks_cluster = cluster
        self.alb_chart = alb_chart
        self.vpc = vpc
