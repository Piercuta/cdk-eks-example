# EKS Fargate Cluster Setup

This directory contains two different EKS cluster configurations:

## 1. EC2-based EKS Cluster (`eks_cluster_stack.py`)
- Uses EC2 instances as worker nodes
- Manual node group management
- More control over instance types and configurations
- Cost-effective for predictable workloads

## 2. Fargate-based EKS Cluster (`eks_fargate_cluster_stack.py`)
- Serverless compute using AWS Fargate
- No EC2 instances to manage
- Pay-per-pod pricing model
- Automatic scaling based on demand

## Key Differences

### EC2 Cluster Features:
- `eks.Cluster` with `default_capacity=2`
- Uses `t3.large` instances
- Manual node group scaling
- More predictable costs

### Fargate Cluster Features:
- `eks.FargateCluster` 
- Fargate profiles for different namespaces
- Automatic pod scaling
- Pay-per-pod pricing

## Fargate Profiles

The Fargate cluster includes three profiles:

1. **SystemProfile**: For `kube-system` namespace
2. **AppProfile**: For application workloads (`default`, `fastapi`, `application`)
3. **MonitoringProfile**: For monitoring (`amazon-cloudwatch`, `monitoring`, `logging`)

## Deployment

### For EC2-based cluster:
```bash
cdk deploy --profile piercuta-dev
```

### For Fargate-based cluster:
```bash
cdk deploy --app "python3 app_fargate.py" --profile piercuta-dev
```

## FastAPI Service Differences

### EC2 Service (`eks_fast_api_service_stack.py`):
- Standard Kubernetes deployment
- Uses node ports for service exposure

### Fargate Service (`eks_fargate_fastapi_service_stack.py`):
- Fargate-optimized deployment
- Resource requests/limits required
- IP-based load balancing
- Enhanced health checks and autoscaling

## Resource Requirements for Fargate

Fargate pods must specify resource requests and limits:

```yaml
resources:
  requests:
    cpu: "250m"
    memory: "512Mi"
  limits:
    cpu: "500m"
    memory: "1Gi"
```

## Cost Considerations

### EC2 Cluster:
- Pay for EC2 instances 24/7
- More cost-effective for steady workloads
- Requires capacity planning

### Fargate Cluster:
- Pay only for running pods
- More expensive per CPU/memory unit
- Better for variable workloads
- No idle costs

## Monitoring and Logging

Both clusters include:
- CloudWatch Agent for metrics
- ALB Controller for load balancing
- Metrics Server for HPA
- Comprehensive logging

## Security

Both configurations include:
- IAM role mappings for SSO access
- Service accounts with least privilege
- Network policies (Fargate version)
- Pod disruption budgets

## Recommendations

### Use EC2 Cluster when:
- You have predictable, steady workloads
- Cost optimization is critical
- You need specific instance types
- You want more control over the infrastructure

### Use Fargate Cluster when:
- You have variable workloads
- You want to avoid infrastructure management
- You prefer pay-per-use pricing
- You need rapid scaling capabilities 