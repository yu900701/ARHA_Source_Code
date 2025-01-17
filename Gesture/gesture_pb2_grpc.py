# Generated by the gRPC Python protocol compiler plugin. DO NOT EDIT!
"""Client and server classes corresponding to protobuf-defined services."""
import grpc
import warnings

import gesture_pb2 as gesture__pb2

GRPC_GENERATED_VERSION = '1.66.2'
GRPC_VERSION = grpc.__version__
_version_not_supported = False

try:
    from grpc._utilities import first_version_is_lower
    _version_not_supported = first_version_is_lower(GRPC_VERSION, GRPC_GENERATED_VERSION)
except ImportError:
    _version_not_supported = True

if _version_not_supported:
    raise RuntimeError(
        f'The grpc package installed is at version {GRPC_VERSION},'
        + f' but the generated code in gesture_pb2_grpc.py depends on'
        + f' grpcio>={GRPC_GENERATED_VERSION}.'
        + f' Please upgrade your grpc module to grpcio>={GRPC_GENERATED_VERSION}'
        + f' or downgrade your generated code using grpcio-tools<={GRPC_VERSION}.'
    )


class GestureRecognitionStub(object):
    """The gesture recognition service definition.
    """

    def __init__(self, channel):
        """Constructor.

        Args:
            channel: A grpc.Channel.
        """
        self.Recognition = channel.unary_unary(
                '/GestureRecognition/Recognition',
                request_serializer=gesture__pb2.RecognitionRequest.SerializeToString,
                response_deserializer=gesture__pb2.RecognitionReply.FromString,
                _registered_method=True)


class GestureRecognitionServicer(object):
    """The gesture recognition service definition.
    """

    def Recognition(self, request, context):
        """Sends an image
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')


def add_GestureRecognitionServicer_to_server(servicer, server):
    rpc_method_handlers = {
            'Recognition': grpc.unary_unary_rpc_method_handler(
                    servicer.Recognition,
                    request_deserializer=gesture__pb2.RecognitionRequest.FromString,
                    response_serializer=gesture__pb2.RecognitionReply.SerializeToString,
            ),
    }
    generic_handler = grpc.method_handlers_generic_handler(
            'GestureRecognition', rpc_method_handlers)
    server.add_generic_rpc_handlers((generic_handler,))
    server.add_registered_method_handlers('GestureRecognition', rpc_method_handlers)


 # This class is part of an EXPERIMENTAL API.
class GestureRecognition(object):
    """The gesture recognition service definition.
    """

    @staticmethod
    def Recognition(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/GestureRecognition/Recognition',
            gesture__pb2.RecognitionRequest.SerializeToString,
            gesture__pb2.RecognitionReply.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)
