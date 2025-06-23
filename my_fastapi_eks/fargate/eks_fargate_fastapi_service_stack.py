from aws_cdk import Stack
from aws_cdk import aws_eks as eks
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from constructs import Construct
from aws_cdk import Duration
import json


class EksFargateFastApiServiceStack(Stack):

    def __init__(
            self,
            scope: Construct,
            construct_id: str,
            cluster: eks.FargateCluster,
            alb_chart: eks.HelmChart,
            **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.cluster = cluster

        # 1. FastAPI Deployment for Fargate
        # Note: Fargate requires specific resource requests and limits
        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "fastapi-app",
                "namespace": "fastapi",
                "labels": {
                    "app": "fastapi"
                }
            },
            "spec": {
                "replicas": 1,
                "selector": {
                    "matchLabels": {
                        "app": "fastapi"
                    }
                },
                "template": {
                    "metadata": {
                        "labels": {
                            "app": "fastapi"
                        }
                    },
                    "spec": {
                        "containers": [{
                            "name": "fastapi",
                            "image": "532673134317.dkr.ecr.eu-west-1.amazonaws.com/services/eks/fastapi_hello_world:latest",
                            "ports": [{"containerPort": 8000}],
                            "resources": {
                                "requests": {
                                    "cpu": "250m",
                                    "memory": "512Mi"
                                },
                                "limits": {
                                    "cpu": "500m",
                                    "memory": "1Gi"
                                }
                            },
                            # "livenessProbe": {
                            #     "httpGet": {
                            #         "path": "/health",
                            #         "port": 8000
                            #     },
                            #     "initialDelaySeconds": 30,
                            #     "periodSeconds": 10
                            # },
                            # "readinessProbe": {
                            #     "httpGet": {
                            #         "path": "/health",
                            #         "port": 8000
                            #     },
                            #     "initialDelaySeconds": 5,
                            #     "periodSeconds": 5
                            # },
                            "env": [
                                {
                                    "name": "ENVIRONMENT",
                                    "value": "production"
                                }
                            ]
                        }]
                    }
                }
            }
        }
        fastapi_deployment = cluster.add_manifest("FastApiDeployment", deployment)

        # 3. FastAPI Service - Changer en ClusterIP
        service = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": "fastapi-service",
                "namespace": "fastapi"
                # Supprimer les annotations ALB
            },
            "spec": {
                "selector": {
                    "app": "fastapi"
                },
                "ports": [{
                    "port": 80,
                    "targetPort": 8000,
                    "protocol": "TCP"
                }],
                "type": "ClusterIP"  # Au lieu de LoadBalancer
            }
        }
        fastapi_service = cluster.add_manifest("FastApiService", service)

        # 4. Ingress for FastAPI (using ALB Controller)
        ingress = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": "fastapi-ingress",
                "namespace": "fastapi",
                "annotations": {
                    "alb.ingress.kubernetes.io/scheme": "internet-facing",
                    "alb.ingress.kubernetes.io/target-type": "ip",
                    "alb.ingress.kubernetes.io/listen-ports": '[{"HTTP": 80}, {"HTTPS": 443}]',
                    "alb.ingress.kubernetes.io/certificate-arn": "arn:aws:acm:eu-west-1:532673134317:certificate/905d0d16-87e8-4e89-a88c-b6053f472e81",
                    "alb.ingress.kubernetes.io/ssl-redirect": "443",
                }
            },
            "spec": {
                "ingressClassName": "alb",
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
                    "hosts": ["fargate-eks-fastapi.piercuta.com"]
                }]
            }
        }
        fastapi_ingress = cluster.add_manifest("FastApiIngress", ingress)

        # 5. Horizontal Pod Autoscaler for Fargate
        # Useless with fargate !
        hpa = {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {
                "name": "fastapi-hpa",
                "namespace": "fastapi"
            },
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": "fastapi-app"
                },
                "minReplicas": 1,
                "maxReplicas": 5,
                "metrics": [
                    {
                        "type": "Resource",
                        "resource": {
                            "name": "cpu",
                            "target": {
                                "type": "Utilization",
                                "averageUtilization": 70
                            }
                        }
                    }
                ]
            }
        }
        fastapi_hpa = cluster.add_manifest("FastApiHPA", hpa)

        # Store references for potential use in other stacks
        fastapi_deployment.node.add_dependency(alb_chart)
        fastapi_service.node.add_dependency(fastapi_deployment)
        fastapi_hpa.node.add_dependency(fastapi_service)
        fastapi_ingress.node.add_dependency(fastapi_hpa)

        # 5. A Record pointant vers l'ALB
        hosted_zone = route53.HostedZone.from_lookup(
            self, "HostedZone",
            domain_name="piercuta.com"
        )

        recort_set = route53.CnameRecord(
            self, "FastApiCnameRecord",
            zone=hosted_zone,
            record_name="fargate-eks-fastapi",
            domain_name=cluster.get_ingress_load_balancer_address(
                ingress_name="fastapi-ingress",
                namespace="fastapi"
            ),
            ttl=Duration.minutes(5)
        )

        recort_set.node.add_dependency(fastapi_ingress)
