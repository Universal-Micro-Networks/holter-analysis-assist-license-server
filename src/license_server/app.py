import logging

from flask import Flask, Response
from werkzeug.exceptions import HTTPException

from license_server.console import dependencies as console_dependencies_module
from license_server.console.dependencies import ConsoleDependencyFactory
from license_server.console.errors import console_error_response, install_console_response_hooks, is_console_request
from license_server.console.routes import console
from license_server.domain.errors import ErrorCode, ServiceError
from license_server.http.access_log import install_access_log
from license_server.http.admin_routes import admin_api
from license_server.http.client_routes import client_api
from license_server.http.dependencies import DependencyFactory, install
from license_server.http.responses import error_response, success_response
from license_server.repository.base import RepositoryUnavailable

logger = logging.getLogger(__name__)


def create_app(
    dependencies: DependencyFactory | None = None, console_dependencies: ConsoleDependencyFactory | None = None
) -> Flask:
    app = Flask(__name__)
    install(app.extensions, dependencies)
    console_dependencies_module.install(app.extensions, console_dependencies)
    install_access_log(app)
    install_console_response_hooks(app)
    _register_error_handlers(app)
    app.register_blueprint(client_api)
    app.register_blueprint(admin_api)
    app.register_blueprint(console)

    @app.get("/healthz")
    def healthz() -> Response:
        return success_response({"status": "ok"})

    return app


def _register_error_handlers(app: Flask) -> None:
    # /console answers with HTML pages; everything else keeps the JSON error format of the API.
    @app.errorhandler(ServiceError)
    def handle_service_error(error: ServiceError) -> Response:
        if is_console_request():
            return console_error_response(error)
        return error_response(error.code, error.message)

    @app.errorhandler(HTTPException)
    def handle_http_exception(error: HTTPException) -> Response:
        if is_console_request():
            return console_error_response(error)
        return error_response(
            ErrorCode.INVALID_REQUEST,
            ServiceError(ErrorCode.INVALID_REQUEST).message,
            status=error.code or 400,
        )

    @app.errorhandler(RepositoryUnavailable)
    def handle_repository_unavailable(error: RepositoryUnavailable) -> Response:
        if is_console_request():
            return console_error_response(error)
        logger.error("repository unavailable: %s", error)
        return _temporary_failure()

    @app.errorhandler(Exception)
    def handle_unexpected(error: Exception) -> Response:
        if is_console_request():
            return console_error_response(error)
        logger.exception("unexpected error")
        return _temporary_failure()


def _temporary_failure() -> Response:
    return error_response(ErrorCode.TEMPORARY_FAILURE, ServiceError(ErrorCode.TEMPORARY_FAILURE).message)
