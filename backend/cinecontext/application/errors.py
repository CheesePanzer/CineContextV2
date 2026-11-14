from alcedo import BadGatewayError, GatewayTimeoutError


class ModelGatewayError(BadGatewayError):
    pass


class ModelGatewayTimeoutError(GatewayTimeoutError):
    pass
