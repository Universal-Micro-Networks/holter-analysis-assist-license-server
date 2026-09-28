from workers import wsgi

from license_server.app import create_app
from license_server.http.dependencies import workers_dependencies

Default = wsgi.entrypoint(create_app(workers_dependencies))
