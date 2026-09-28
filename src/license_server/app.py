import logging

from flask import Flask, Response
from werkzeug.exceptions import HTTPException

from license_server.domain.errors import ErrorCode, ServiceError
from license_server.http.access_log import install_access_log
from license_server.http.admin_routes import admin_api
from license_server.http.client_routes import client_api
from license_server.http.dependencies import DependencyFactory, install
from license_server.http.responses import error_response, success_response
from license_server.repository.base import RepositoryUnavailable

logger = logging.getLogger(__name__)


def create_app(dependencies: DependencyFactory | None = None) -> Flask:
    app = Flask(__name__)
    install(app.extensions, dependencies)
    install_access_log(app)
    _register_error_handlers(app)
    app.register_blueprint(client_api)
    app.register_blueprint(admin_api)

    @app.get("/healthz")
    def healthz() -> Response:
        return success_response({"status": "ok"})

    return app


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ServiceError)
    def handle_service_error(error: ServiceError) -> Response:
        return error_response(error.code, error.message)

    @app.errorhandler(HTTPException)
    def handle_http_exception(error: HTTPException) -> Response:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            ServiceError(ErrorCode.INVALID_REQUEST).message,
            status=error.code or 400,
        )

    @app.errorhandler(RepositoryUnavailable)
    def handle_repository_unavailable(error: RepositoryUnavailable) -> Response:
        logger.error("repository unavailable: %s", error)
        return _temporary_failure()

    @app.errorhandler(Exception)
    def handle_unexpected(error: Exception) -> Response:
        logger.exception("unexpected error")
        return _temporary_failure()


def _temporary_failure() -> Response:
    return error_response(ErrorCode.TEMPORARY_FAILURE, ServiceError(ErrorCode.TEMPORARY_FAILURE).message)
