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
            default_capacity_instance=ec2.InstanceType("t3.small"),
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

        # 3. CloudWatch Agent
        cloudwatch_policy_doc = iam.PolicyDocument.from_json(
            json.load(open("policy/cloudwatch-logs-policy.json"))
        )

        cloudwatch_sa = cluster.add_service_account(
            "CloudWatchAgentSA",
            name="cloudwatch-agent",
            namespace="amazon-cloudwatch"
        )

        cloudwatch_sa.role.attach_inline_policy(
            iam.Policy(self, "CloudWatchPolicy", document=cloudwatch_policy_doc)
        )

        cluster.add_helm_chart(
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

        # cluster.add_helm_chart(
        #     "FluentBitChart",
        #     chart="aws-for-fluent-bit",
        #     release="fluent-bit",
        #     repository="https://aws.github.io/eks-charts",
        #     namespace="amazon-cloudwatch",
        #     values={
        #         "serviceAccount": {
        #             "create": False,
        #             "name": "cloudwatch-agent"
        #         },
        #         "cloudWatch": {
        #             "enabled": True,
        #             "logGroupName": f"/aws/eks/{cluster.cluster_name}/application",
        #             "region": "eu-west-1"
        #         },
        #         "firehose": {"enabled": False},
        #         "kinesis": {"enabled": False},
        #         "elasticsearch": {"enabled": False}
        #     }
        # )

        cluster.add_helm_chart(
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

        ingress = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": "fastapi-ingress",
                "annotations": {
                    "kubernetes.io/ingress.class": "alb",
                    "alb.ingress.kubernetes.io/scheme": "internet-facing",
                    "alb.ingress.kubernetes.io/target-type": "ip",
                    "alb.ingress.kubernetes.io/listen-ports": '[{"HTTP": 80, "HTTPS": 443}]',
                    "alb.ingress.kubernetes.io/certificate-arn": "arn:aws:acm:eu-west-1:532673134317:certificate/905d0d16-87e8-4e89-a88c-b6053f472e81",
                    "alb.ingress.kubernetes.io/ssl-redirect": "443"
                }
            },
            "spec": {
                "rules": [{
                    "http": {
                        "paths": [{
                            "path": "/",
                            "pathType": "Prefix",
                            "backend": {
                                "service": {
                                    "name": "fastapi-service",
                                    "port": {
                                        "number": 80
                                    }
                                }
                            }
                        }]
                    }
                }],
                "tls": [{
                    "hosts": ["my-fastapi.piercuta.com"]
                }]
            }
        }

        # 4. Apply les manifests
        cluster.add_manifest("FastApiDeployment", deployment)
        cluster.add_manifest("FastApiService", service)
        cluster.add_manifest("FastApiIngress", ingress)

        # 5. A Record pointant vers l'ALB
        hosted_zone = route53.HostedZone.from_lookup(
            self, "HostedZone",
            domain_name="piercuta.com"
        )

        # route53.ARecord(
        #     self, "FastApiAliasRecord",
        #     zone=hosted_zone,
        #     record_name="my-fastapi",
        #     target=route53.RecordTarget.from_values(
        #         cluster.get_ingress_load_balancer_address("FastApiIngress")
        #     )
        # )

        route53.CnameRecord(
            self, "FastApiCnameRecord",
            zone=hosted_zone,
            record_name="my-fastapi",  # Cela crée my-fastapi.piercuta.com
            domain_name="k8s-default-fastapii-3541f9c717-272254031.eu-west-1.elb.amazonaws.com",  # <--- Remplace par le bon DNS ALB
            ttl=Duration.minutes(5)
        )
