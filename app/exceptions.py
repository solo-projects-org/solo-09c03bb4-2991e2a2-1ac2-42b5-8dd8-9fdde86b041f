"""业务异常，main 中统一映射为 HTTP 状态码。"""


class ApiError(Exception):
    status_code = 400

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class NotFoundError(ApiError):
    status_code = 404


class ConflictError(ApiError):
    status_code = 409
