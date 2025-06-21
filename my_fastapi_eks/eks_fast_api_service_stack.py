from aws_cdk import Stack
from aws_cdk import aws_eks as eks
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from aws_cdk import Duration
from constructs import Construct


class EksFastApiServiceStack(Stack):

    def __init__(self,
                 scope: Construct,
                 construct_id: str,
                 cluster: eks.Cluster,
                 alb_chart: eks.HelmChart,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Déploiement FastAPI depuis une image ECR
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
                            "ports": [{"containerPort": 8000}],
                            "resources": {
                                "requests": {
                                    "cpu": "100m",
                                    "memory": "128Mi"
                                },
                                "limits": {
                                    "cpu": "500m",
                                    "memory": "256Mi"
                                }
                            }
                        }]
                    }
                }
            }
        }

        hpa = {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {
                "name": "fastapi-hpa"
            },
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": "fastapi"
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
                                "averageUtilization": 50
                            }
                        }
                    }
                ]
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
                    "alb.ingress.kubernetes.io/scheme": "internet-facing",
                    "alb.ingress.kubernetes.io/target-type": "ip",
                    "alb.ingress.kubernetes.io/listen-ports": '[{"HTTP": 80, "HTTPS": 443}]',
                    "alb.ingress.kubernetes.io/certificate-arn": "arn:aws:acm:eu-west-1:532673134317:certificate/905d0d16-87e8-4e89-a88c-b6053f472e81",
                    "alb.ingress.kubernetes.io/ssl-redirect": "443"
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
                    "hosts": ["my-fastapi.piercuta.com"]
                }]
            }
        }

        # 4. Apply les manifests
        # cluster.add_manifest(
        #     "FastApiDeployment",
        #     deployment,
        #     service,
        #     ingress
        # )

        fastapi_deployment = cluster.add_manifest("FastApiDeployment", deployment)
        fastapi_service = cluster.add_manifest("FastApiService", service)
        fastapi_ingress = cluster.add_manifest("FastApiIngress", ingress)
        fastapi_hpa = cluster.add_manifest("FastApiHPA", hpa)

        # Ordre logique :
        fastapi_deployment.node.add_dependency(alb_chart)

        fastapi_service.node.add_dependency(fastapi_deployment)

        fastapi_ingress.node.add_dependency(alb_chart)
        fastapi_ingress.node.add_dependency(fastapi_service)
        fastapi_ingress.node.add_dependency(fastapi_deployment)
        fastapi_hpa.node.add_dependency(fastapi_service)

        # 5. A Record pointant vers l'ALB
        hosted_zone = route53.HostedZone.from_lookup(
            self, "HostedZone",
            domain_name="piercuta.com"
        )

        recort_set = route53.CnameRecord(
            self, "FastApiCnameRecord",
            zone=hosted_zone,
            record_name="my-fastapi",
            domain_name=cluster.get_ingress_load_balancer_address(
                ingress_name="fastapi-ingress",
                namespace="default"
            ),
            ttl=Duration.minutes(5)
        )

        recort_set.node.add_dependency(fastapi_ingress)
