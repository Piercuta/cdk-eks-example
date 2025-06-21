import aws_cdk as core
import aws_cdk.assertions as assertions

from my_fastapi_eks.my_fastapi_eks_stack import MyFastapiEksStack

# example tests. To run these tests, uncomment this file along with the example
# resource in my_fastapi_eks/my_fastapi_eks_stack.py
def test_sqs_queue_created():
    app = core.App()
    stack = MyFastapiEksStack(app, "my-fastapi-eks")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })
