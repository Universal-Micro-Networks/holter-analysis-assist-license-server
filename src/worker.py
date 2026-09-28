from workers import wsgi

from license_server.app import create_app
from license_server.wiring import workers_console_dependencies, workers_dependencies

Default = wsgi.entrypoint(create_app(workers_dependencies, workers_console_dependencies))
