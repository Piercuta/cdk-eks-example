from constructs import Construct
from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_eks_v2_alpha as eks_alpha,
    RemovalPolicy,
    aws_codebuild as codebuild,
)
from aws_cdk.lambda_layer_kubectl_v32 import KubectlV32Layer
import json
import yaml
from aws_cdk import Tags


class CdkEksKarpenterStack(Stack):

    def __init__(self, scope: Construct,
                 construct_id: str,
                 codebuild_project: codebuild.Project,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.codebuild_project = codebuild_project

        self.cluster_name = "karpenter-eks-cluster"
        self.vpc = self.create_vpc()
        self.eks_cluster = self.create_eks_cluster()
        # tagging ne fonctionne pas ---> via codebuild instead
        Tags.of(self.eks_cluster.cluster_security_group).add("karpenter.sh/discovery", self.cluster_name)
        Tags.of(self.eks_cluster.cluster_security_group).add("kubernetes.io/cluster/" + self.cluster_name, "owned")
        self.node_group = self.create_node_group()
        self.add_access_entry()
        self.karpenter_chart = self.create_karpenter_chart()
        self.karpenter_node_role = self.create_karpenter_node_role_mapping()
        # self.karpenter_node_pool = self.create_karpenter_node_pool()

    def create_vpc(self) -> ec2.Vpc:
        vpc = ec2.Vpc(
            self, "EksVpc",
            nat_gateways=1,
            max_azs=3,
            reserved_azs=3,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                    map_public_ip_on_launch=True,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
            enable_dns_hostnames=True,
            enable_dns_support=True,
        )

        # very important to add tags subnets for Karpenter discovery
        for subnet in vpc.private_subnets:
            Tags.of(subnet).add("karpenter.sh/discovery", self.cluster_name)

        # for subnet in vpc.public_subnets:
        #     Tags.of(subnet).add("karpenter.sh/discovery", self.cluster_name)

        return vpc

    def create_eks_cluster(self) -> eks_alpha.Cluster:
        cluster = eks_alpha.Cluster(
            self, "KarpenterCluster",
            version=eks_alpha.KubernetesVersion.V1_32,
            kubectl_provider_options=eks_alpha.KubectlProviderOptions(
                kubectl_layer=KubectlV32Layer(self, "kubectl"),
            ),
            default_capacity_type=eks_alpha.DefaultCapacityType.NODEGROUP,
            default_capacity=0,
            vpc=self.vpc,
            cluster_name=self.cluster_name,
            cluster_logging=[
                eks_alpha.ClusterLoggingTypes.API,
                eks_alpha.ClusterLoggingTypes.AUDIT,
                eks_alpha.ClusterLoggingTypes.AUTHENTICATOR,
                eks_alpha.ClusterLoggingTypes.CONTROLLER_MANAGER,
                eks_alpha.ClusterLoggingTypes.SCHEDULER
            ],
            alb_controller=eks_alpha.AlbControllerOptions(
                version=eks_alpha.AlbControllerVersion.V2_8_2
            ),
            tags={
                # hope it will propagate to the security group
                "karpenter.sh/discovery": self.cluster_name
            }
        )

        return cluster

    def create_node_group(self):
        return self.eks_cluster.add_nodegroup_capacity(
            "DefaultNodeGroup",
            ami_type=eks_alpha.NodegroupAmiType.AL2023_X86_64_STANDARD,
            desired_size=1,
            instance_types=[ec2.InstanceType.of(ec2.InstanceClass.M5, ec2.InstanceSize.XLARGE)],
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            labels={
                "node.kubernetes.io/role": "worker",
                "karpenter.sh/capacity-type": "on-demand",
            },
            tags={
                "k8s.io/cluster-autoscaler/node-template/label/node.kubernetes.io/role": "worker",
                "k8s.io/cluster-autoscaler/node-template/label/karpenter.sh/capacity-type": "on-demand",
            },
            capacity_type=eks_alpha.CapacityType.ON_DEMAND,
            remote_access=eks_alpha.NodegroupRemoteAccess(
                ssh_key_name="piercuta-key"
            )
        )

    def add_access_entry(self):
        self.eks_cluster.grant_cluster_admin(
            id="SSOAdminRole",
            principal="arn:aws:iam::532673134317:role/aws-reserved/sso.amazonaws.com/eu-west-1/AWSReservedSSO_AdministratorAccess_ecdb820f0c77380d",
        )

        self.eks_cluster.grant_cluster_admin(
            id="codebuild-project-role",
            principal=self.codebuild_project.role.role_arn,
        )

    def create_karpenter_chart(self):
        karpenter_ns = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": "karpenter"
            }
        }
        karpenter_namespace = self.eks_cluster.add_manifest("KarpenterNamespace", karpenter_ns)

        karpenter_namespace.node.add_dependency(self.node_group)

        karpenter_sa = self.eks_cluster.add_service_account(
            "karpenter-sa",
            name="karpenter",
            namespace="karpenter",
            labels={
                "app.kubernetes.io/managed-by": "Helm"
            },
            annotations={
                "meta.helm.sh/release-name": "karpenter",
                "meta.helm.sh/release-namespace": "karpenter"
            }
        )

        # Create custom Karpenter controller policy based on official CloudFormation
        karpenter_controller_policy = iam.ManagedPolicy(
            self, "KarpenterControllerPolicy",
            managed_policy_name=f"KarpenterControllerPolicy-{self.cluster_name}",
            statements=[
                # AllowScopedEC2InstanceAccessActions
                iam.PolicyStatement(
                    sid="AllowScopedEC2InstanceAccessActions",
                    effect=iam.Effect.ALLOW,
                    resources=[
                        f"arn:{self.partition}:ec2:{self.region}::image/*",
                        f"arn:{self.partition}:ec2:{self.region}::snapshot/*",
                        f"arn:{self.partition}:ec2:{self.region}:*:security-group/*",
                        f"arn:{self.partition}:ec2:{self.region}:*:subnet/*",
                        f"arn:{self.partition}:ec2:{self.region}:*:capacity-reservation/*"
                    ],
                    actions=[
                        "ec2:RunInstances",
                        "ec2:CreateFleet"
                    ]
                ),
                # AllowScopedEC2LaunchTemplateAccessActions
                iam.PolicyStatement(
                    sid="AllowScopedEC2LaunchTemplateAccessActions",
                    effect=iam.Effect.ALLOW,
                    resources=[f"arn:{self.partition}:ec2:{self.region}:*:launch-template/*"],
                    actions=[
                        "ec2:RunInstances",
                        "ec2:CreateFleet"
                    ],
                    conditions={
                        "StringEquals": {
                            f"aws:ResourceTag/kubernetes.io/cluster/{self.cluster_name}": "owned"
                        },
                        "StringLike": {
                            "aws:ResourceTag/karpenter.sh/nodepool": "*"
                        }
                    }
                ),
                # AllowScopedEC2InstanceActionsWithTags
                iam.PolicyStatement(
                    sid="AllowScopedEC2InstanceActionsWithTags",
                    effect=iam.Effect.ALLOW,
                    resources=[
                        f"arn:{self.partition}:ec2:{self.region}:*:fleet/*",
                        f"arn:{self.partition}:ec2:{self.region}:*:instance/*",
                        f"arn:{self.partition}:ec2:{self.region}:*:volume/*",
                        f"arn:{self.partition}:ec2:{self.region}:*:network-interface/*",
                        f"arn:{self.partition}:ec2:{self.region}:*:launch-template/*",
                        f"arn:{self.partition}:ec2:{self.region}:*:spot-instances-request/*",
                        f"arn:{self.partition}:ec2:{self.region}:*:capacity-reservation/*"
                    ],
                    actions=[
                        "ec2:RunInstances",
                        "ec2:CreateFleet",
                        "ec2:CreateLaunchTemplate"
                    ],
                    conditions={
                        "StringEquals": {
                            f"aws:RequestTag/kubernetes.io/cluster/{self.cluster_name}": "owned",
                            f"aws:RequestTag/eks:eks-cluster-name": self.cluster_name
                        },
                        "StringLike": {
                            "aws:RequestTag/karpenter.sh/nodepool": "*"
                        }
                    }
                ),
                # AllowScopedResourceCreationTagging
                iam.PolicyStatement(
                    sid="AllowScopedResourceCreationTagging",
                    effect=iam.Effect.ALLOW,
                    resources=[
                        f"arn:{self.partition}:ec2:{self.region}:*:fleet/*",
                        f"arn:{self.partition}:ec2:{self.region}:*:instance/*",
                        f"arn:{self.partition}:ec2:{self.region}:*:volume/*",
                        f"arn:{self.partition}:ec2:{self.region}:*:network-interface/*",
                        f"arn:{self.partition}:ec2:{self.region}:*:launch-template/*",
                        f"arn:{self.partition}:ec2:{self.region}:*:spot-instances-request/*"
                    ],
                    actions=["ec2:CreateTags"],
                    conditions={
                        "StringEquals": {
                            f"aws:RequestTag/kubernetes.io/cluster/{self.cluster_name}": "owned",
                            f"aws:RequestTag/eks:eks-cluster-name": self.cluster_name,
                            "ec2:CreateAction": [
                                "RunInstances",
                                "CreateFleet",
                                "CreateLaunchTemplate"
                            ]
                        },
                        "StringLike": {
                            "aws:RequestTag/karpenter.sh/nodepool": "*"
                        }
                    }
                ),
                # AllowScopedResourceTagging
                iam.PolicyStatement(
                    sid="AllowScopedResourceTagging",
                    effect=iam.Effect.ALLOW,
                    resources=[f"arn:{self.partition}:ec2:{self.region}:*:instance/*"],
                    actions=["ec2:CreateTags"],
                    conditions={
                        "StringEquals": {
                            f"aws:ResourceTag/kubernetes.io/cluster/{self.cluster_name}": "owned"
                        },
                        "StringLike": {
                            "aws:ResourceTag/karpenter.sh/nodepool": "*"
                        },
                        "StringEqualsIfExists": {
                            f"aws:RequestTag/eks:eks-cluster-name": self.cluster_name
                        },
                        "ForAllValues:StringEquals": {
                            "aws:TagKeys": [
                                "eks:eks-cluster-name",
                                "karpenter.sh/nodeclaim",
                                "Name"
                            ]
                        }
                    }
                ),
                # AllowScopedDeletion
                iam.PolicyStatement(
                    sid="AllowScopedDeletion",
                    effect=iam.Effect.ALLOW,
                    resources=[
                        f"arn:{self.partition}:ec2:{self.region}:*:instance/*",
                        f"arn:{self.partition}:ec2:{self.region}:*:launch-template/*"
                    ],
                    actions=[
                        "ec2:TerminateInstances",
                        "ec2:DeleteLaunchTemplate"
                    ],
                    conditions={
                        "StringEquals": {
                            f"aws:ResourceTag/kubernetes.io/cluster/{self.cluster_name}": "owned"
                        },
                        "StringLike": {
                            "aws:ResourceTag/karpenter.sh/nodepool": "*"
                        }
                    }
                ),
                # AllowRegionalReadActions
                iam.PolicyStatement(
                    sid="AllowRegionalReadActions",
                    effect=iam.Effect.ALLOW,
                    resources=["*"],
                    actions=[
                        "ec2:DescribeCapacityReservations",
                        "ec2:DescribeImages",
                        "ec2:DescribeInstances",
                        "ec2:DescribeInstanceTypeOfferings",
                        "ec2:DescribeInstanceTypes",
                        "ec2:DescribeLaunchTemplates",
                        "ec2:DescribeSecurityGroups",
                        "ec2:DescribeSpotPriceHistory",
                        "ec2:DescribeSubnets"
                    ],
                    conditions={
                        "StringEquals": {
                            "aws:RequestedRegion": self.region
                        }
                    }
                ),
                # AllowSSMReadActions
                iam.PolicyStatement(
                    sid="AllowSSMReadActions",
                    effect=iam.Effect.ALLOW,
                    resources=[f"arn:{self.partition}:ssm:{self.region}::parameter/aws/service/*"],
                    actions=["ssm:GetParameter"]
                ),
                # AllowPricingReadActions
                iam.PolicyStatement(
                    sid="AllowPricingReadActions",
                    effect=iam.Effect.ALLOW,
                    resources=["*"],
                    actions=["pricing:GetProducts"]
                ),
                # AllowPassingInstanceRole
                iam.PolicyStatement(
                    sid="AllowPassingInstanceRole",
                    effect=iam.Effect.ALLOW,
                    resources=[
                        f"arn:{
                            self.partition}:iam::{
                            self.account}:role/KarpenterNodeRole-{
                            self.cluster_name}"],
                    actions=["iam:PassRole"],
                    conditions={
                        "StringEquals": {
                            "iam:PassedToService": [
                                "ec2.amazonaws.com",
                                "ec2.amazonaws.com.cn"
                            ]
                        }
                    }
                ),
                # AllowScopedInstanceProfileCreationActions
                iam.PolicyStatement(
                    sid="AllowScopedInstanceProfileCreationActions",
                    effect=iam.Effect.ALLOW,
                    resources=[f"arn:{self.partition}:iam::{self.account}:instance-profile/*"],
                    actions=["iam:CreateInstanceProfile"],
                    conditions={
                        "StringEquals": {
                            f"aws:RequestTag/kubernetes.io/cluster/{self.cluster_name}": "owned",
                            f"aws:RequestTag/eks:eks-cluster-name": self.cluster_name,
                            f"aws:RequestTag/topology.kubernetes.io/region": self.region
                        },
                        "StringLike": {
                            "aws:RequestTag/karpenter.k8s.aws/ec2nodeclass": "*"
                        }
                    }
                ),
                # AllowScopedInstanceProfileTagActions
                iam.PolicyStatement(
                    sid="AllowScopedInstanceProfileTagActions",
                    effect=iam.Effect.ALLOW,
                    resources=[f"arn:{self.partition}:iam::{self.account}:instance-profile/*"],
                    actions=["iam:TagInstanceProfile"],
                    conditions={
                        "StringEquals": {
                            f"aws:ResourceTag/kubernetes.io/cluster/{self.cluster_name}": "owned",
                            f"aws:ResourceTag/topology.kubernetes.io/region": self.region,
                            f"aws:RequestTag/kubernetes.io/cluster/{self.cluster_name}": "owned",
                            f"aws:RequestTag/eks:eks-cluster-name": self.cluster_name,
                            f"aws:RequestTag/topology.kubernetes.io/region": self.region
                        },
                        "StringLike": {
                            "aws:ResourceTag/karpenter.k8s.aws/ec2nodeclass": "*",
                            "aws:RequestTag/karpenter.k8s.aws/ec2nodeclass": "*"
                        }
                    }
                ),
                # AllowScopedInstanceProfileActions
                iam.PolicyStatement(
                    sid="AllowScopedInstanceProfileActions",
                    effect=iam.Effect.ALLOW,
                    resources=[f"arn:{self.partition}:iam::{self.account}:instance-profile/*"],
                    actions=[
                        "iam:AddRoleToInstanceProfile",
                        "iam:RemoveRoleFromInstanceProfile",
                        "iam:DeleteInstanceProfile"
                    ],
                    conditions={
                        "StringEquals": {
                            f"aws:ResourceTag/kubernetes.io/cluster/{self.cluster_name}": "owned",
                            f"aws:ResourceTag/topology.kubernetes.io/region": self.region
                        },
                        "StringLike": {
                            "aws:ResourceTag/karpenter.k8s.aws/ec2nodeclass": "*"
                        }
                    }
                ),
                # AllowInstanceProfileReadActions
                iam.PolicyStatement(
                    sid="AllowInstanceProfileReadActions",
                    effect=iam.Effect.ALLOW,
                    resources=[f"arn:{self.partition}:iam::{self.account}:instance-profile/*"],
                    actions=["iam:GetInstanceProfile"]
                ),
                # AllowAPIServerEndpointDiscovery
                iam.PolicyStatement(
                    sid="AllowAPIServerEndpointDiscovery",
                    effect=iam.Effect.ALLOW,
                    resources=[
                        f"arn:{
                            self.partition}:eks:{
                            self.region}:{
                            self.account}:cluster/{
                            self.cluster_name}"],
                    actions=["eks:DescribeCluster"]
                )
            ]
        )

        # Attach the custom policy to the service account
        karpenter_sa.role.add_managed_policy(karpenter_controller_policy)

        karpenter_sa.node.add_dependency(karpenter_namespace)

        karpenter_chart = self.eks_cluster.add_helm_chart(
            "KarpenterHelmChart",
            chart="karpenter",
            # repository="https://charts.karpenter.sh",
            repository="oci://public.ecr.aws/karpenter/karpenter",
            release="karpenter",
            namespace="karpenter",
            version="1.5.0",  # Version spécifique pour la stabilité
            values={
                # "clusterName": self.cluster_name,
                # "clusterEndpoint": self.eks_cluster.cluster_endpoint,
                "settings": {
                    "clusterName": self.cluster_name,
                    "clusterEndpoint": self.eks_cluster.cluster_endpoint,
                    # Supprimer interruptionQueue s'il n'y a pas de queue SQS
                    # "interruptionQueue": ""
                },
                "serviceAccount": {
                    "create": False,
                    "name": "karpenter",
                    # "annotations": {
                    #     "eks.amazonaws.com/role-arn": karpenter_sa.role.role_arn
                    # }
                },
                # "controller": {
                #     "resources": {
                #         "requests": {
                #             "cpu": 1,
                #             "memory": "1Gi"
                #         },
                #         "limits": {
                #             "cpu": 1,
                #             "memory": "1Gi"
                #         }
                #     }
                # }
            }
        )

        karpenter_chart.node.add_dependency(karpenter_sa)

        return karpenter_chart

    def create_karpenter_node_role_mapping(self):
        """Create IAM role for Karpenter-managed nodes"""

        # Créer le rôle pour les nœuds
        karpenter_node_role = iam.Role(
            self, "KarpenterNodeRole",
            role_name=f"KarpenterNodeRole-{self.cluster_name}",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEKS_CNI_Policy"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEKSWorkerNodePolicy"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2ContainerRegistryReadOnly"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")
            ]
        )

        # important because cluster use eks api auth only and not config map
        self.eks_cluster.grant_cluster_admin(
            id="KarpenterNodeRole",
            principal=karpenter_node_role.role_arn,
        )

        # self.eks_cluster.grant_access(
        #     id="KarpenterNodeRole",
        #     principal=karpenter_node_role.role_arn,
        #     access_policies=[
        #         eks_alpha.AccessPolicy.from_access_policy_name(
        #             "AmazonEKSAutoNodePolicy",
        #             access_scope_type=eks_alpha.AccessScopeType.CLUSTER
        #         )
        #     ]
        # )

        # # ✅ Méthode correcte pour les nœuds EC2 when config map working on cluster
        # # Ajouter le mapping dans aws-auth via un manifest Kubernetes
        aws_auth_mapping = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "aws-auth",
                "namespace": "kube-system"
            },
            "data": {
                "mapRoles": f"|-\n  - rolearn: {karpenter_node_role.role_arn}\n    username: system:node:{{{{EC2PrivateDNSName}}}}\n    groups:\n    - system:bootstrappers\n    - system:nodes"
            }
        }

        self.eks_cluster.add_manifest("KarpenterNodeRoleMapping", aws_auth_mapping)

        return karpenter_node_role

    def create_karpenter_node_pool(self):
        """Create Karpenter NodePool and EC2NodeClass from manifest files"""

        # with open("k8s-manifests/karpenter-pool.yaml", "r") as f:
        #     karpenter_node_pool_content = f.read()

        ec2_node_class_manifest = {
            "apiVersion": "karpenter.k8s.aws/v1",
            "kind": "EC2NodeClass",
            "metadata": {
                "name": "default"
            },
            "spec": {
                "role": f"KarpenterNodeRole-{self.cluster_name}",
                "subnetSelectorTerms": [
                    {
                        "tags": {
                            "karpenter.sh/discovery": self.cluster_name
                        }
                    }
                ],
                "securityGroupSelectorTerms": [
                    {
                        "tags": {
                            "karpenter.sh/discovery": self.cluster_name
                        }
                    }
                ],
                "amiFamily": "AL2023",
                "amiSelectorTerms": [
                    {
                        "alias": "al2023@latest"
                    }
                ]
            }
        }
        ec2_node_class_manifest_obj = self.eks_cluster.add_manifest(
            "KarpenterEC2NodeClass", ec2_node_class_manifest)
        ec2_node_class_manifest_obj.node.add_dependency(self.karpenter_chart)

        node_pool_manifest = {
            "apiVersion": "karpenter.sh/v1",
            "kind": "NodePool",
            "metadata": {
                "name": "default"
            },
            "spec": {
                "template": {
                    "spec": {
                        "nodeClassRef": {
                            "group": "karpenter.k8s.aws",
                            "kind": "EC2NodeClass",
                            "name": "default"
                        },
                        "requirements": [
                            {
                                "key": "karpenter.k8s.aws/instance-category",
                                "operator": "In",
                                "values": [
                                    "c",
                                    "m",
                                    "r"
                                ]
                            },
                            {
                                "key": "karpenter.k8s.aws/instance-generation",
                                "operator": "Gt",
                                "values": [
                                    "2"
                                ]
                            },
                            {
                                "key": "karpenter.sh/capacity-type",
                                "operator": "In",
                                "values": [
                                    "on-demand"
                                ]
                            }
                        ]
                    }
                }
            }
        }

        node_pool_manifest_obj = self.eks_cluster.add_manifest(
            "KarpenterNodePool", node_pool_manifest)
        node_pool_manifest_obj.node.add_dependency(ec2_node_class_manifest_obj)

    def create_load_balancer_controller_chart(self):
        aws_load_balancer_controller = eks_alpha.AlbController(
            self, "ALBController",
            cluster=self.eks_cluster,
            version=eks_alpha.AlbControllerVersion.V2_8_2
        )

        return aws_load_balancer_controller

    def create_load_balancer_controller_helm_chart(self):
        # Useless for the moment. We use alb_controller part of the eks_cluster.
        alb_sa = self.eks_cluster.add_service_account(
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

        alb_chart = self.eks_cluster.add_helm_chart(
            "AWSLoadBalancerController",
            chart="aws-load-balancer-controller",
            repository="https://aws.github.io/eks-charts",
            namespace="kube-system",
            release="alb-controller",
            version="1.7.1",  # ou dernière version stable
            values={
                "clusterName": self.eks_cluster.cluster_name,
                "serviceAccount": {
                    "create": False,
                    "name": "aws-load-balancer-controller"
                },
                "region": self.region,
                "vpcId": self.vpc.vpc_id,
                "replicaCount": 1
            }
        )

        return alb_chart
