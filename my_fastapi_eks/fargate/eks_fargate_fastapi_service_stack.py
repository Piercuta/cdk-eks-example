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

    def __init__(self, scope: Construct, construct_id: str, cluster: eks.FargateCluster, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.cluster = cluster

        # 1. Create namespace for FastAPI application
        fastapi_namespace = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": "fastapi",
                "labels": {
                    "name": "fastapi"
                }
            }
        }
        cluster.add_manifest("FastApiNamespace", fastapi_namespace)

        # 2. FastAPI Deployment for Fargate
        # Note: Fargate requires specific resource requests and limits
        fastapi_deployment = {
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
                "replicas": 2,
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
                            "livenessProbe": {
                                "httpGet": {
                                    "path": "/health",
                                    "port": 8000
                                },
                                "initialDelaySeconds": 30,
                                "periodSeconds": 10
                            },
                            "readinessProbe": {
                                "httpGet": {
                                    "path": "/health",
                                    "port": 8000
                                },
                                "initialDelaySeconds": 5,
                                "periodSeconds": 5
                            },
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
        cluster.add_manifest("FastApiDeployment", fastapi_deployment)

        # 3. FastAPI Service
        fastapi_service = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": "fastapi-service",
                "namespace": "fastapi",
                "annotations": {
                    "service.beta.kubernetes.io/aws-load-balancer-type": "nlb",
                    "service.beta.kubernetes.io/aws-load-balancer-scheme": "internet-facing",
                    "service.beta.kubernetes.io/aws-load-balancer-nlb-target-type": "ip"
                }
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
                "type": "LoadBalancer"
            }
        }
        cluster.add_manifest("FastApiService", fastapi_service)

        # 4. Ingress for FastAPI (using ALB Controller)
        fastapi_ingress = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": "fastapi-ingress",
                "namespace": "fastapi",
                "annotations": {
                    "kubernetes.io/ingress.class": "alb",
                    "alb.ingress.kubernetes.io/scheme": "internet-facing",
                    "alb.ingress.kubernetes.io/target-type": "ip",
                    "alb.ingress.kubernetes.io/listen-ports": '[{"HTTP": 80}, {"HTTPS": 443}]',
                    "alb.ingress.kubernetes.io/ssl-redirect": "443",
                    "alb.ingress.kubernetes.io/healthcheck-path": "/health",
                    "alb.ingress.kubernetes.io/healthcheck-port": "8000",
                    "alb.ingress.kubernetes.io/success-codes": "200,302",
                    "alb.ingress.kubernetes.io/group.name": "fastapi",
                    "alb.ingress.kubernetes.io/group.order": "1"
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
                }]
            }
        }
        cluster.add_manifest("FastApiIngress", fastapi_ingress)

        # 5. Horizontal Pod Autoscaler for Fargate
        fastapi_hpa = {
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
                "minReplicas": 2,
                "maxReplicas": 10,
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
                    },
                    {
                        "type": "Resource",
                        "resource": {
                            "name": "memory",
                            "target": {
                                "type": "Utilization",
                                "averageUtilization": 80
                            }
                        }
                    }
                ]
            }
        }
        cluster.add_manifest("FastApiHPA", fastapi_hpa)

        # 6. ConfigMap for FastAPI configuration
        fastapi_config = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "fastapi-config",
                "namespace": "fastapi"
            },
            "data": {
                "APP_HOST": "0.0.0.0",
                "APP_PORT": "8000",
                "LOG_LEVEL": "info"
            }
        }
        cluster.add_manifest("FastApiConfig", fastapi_config)

        # 7. Service Account for FastAPI (if needed for AWS services)
        fastapi_sa = cluster.add_service_account(
            "FastApiServiceAccount",
            name="fastapi-sa",
            namespace="fastapi"
        )

        # 8. Network Policy for FastAPI (optional security)
        fastapi_network_policy = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": "fastapi-network-policy",
                "namespace": "fastapi"
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {
                        "app": "fastapi"
                    }
                },
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [{
                    "ports": [{
                        "protocol": "TCP",
                        "port": 8000
                    }]
                }],
                "egress": [{
                    "to": [{
                        "namespaceSelector": {}
                    }]
                }]
            }
        }
        cluster.add_manifest("FastApiNetworkPolicy", fastapi_network_policy)

        # 9. Pod Disruption Budget for high availability
        fastapi_pdb = {
            "apiVersion": "policy/v1",
            "kind": "PodDisruptionBudget",
            "metadata": {
                "name": "fastapi-pdb",
                "namespace": "fastapi"
            },
            "spec": {
                "minAvailable": 1,
                "selector": {
                    "matchLabels": {
                        "app": "fastapi"
                    }
                }
            }
        }
        cluster.add_manifest("FastApiPDB", fastapi_pdb)

        # Store references for potential use in other stacks
        self.fastapi_namespace = fastapi_namespace
        self.fastapi_deployment = fastapi_deployment
        self.fastapi_service = fastapi_service
        self.fastapi_ingress = fastapi_ingress
