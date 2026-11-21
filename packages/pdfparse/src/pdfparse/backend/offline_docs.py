"""Self-hosted API documentation; no CDN, favicon host or Google Fonts."""
from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html


def install_offline_docs(app: FastAPI, assets: str):
    def swagger(request: Request):
        root = request.scope.get('root_path', '').rstrip('/')
        return get_swagger_ui_html(
            openapi_url=root + app.openapi_url, title=app.title + ' - Swagger UI',
            swagger_js_url=root + assets + '/swagger-ui-bundle.js',
            swagger_css_url=root + assets + '/swagger-ui.css',
            swagger_favicon_url='data:,', oauth2_redirect_url=root + '/docs/oauth2-redirect',
            swagger_ui_parameters={'validatorUrl': None},
        )

    def redoc(request: Request):
        root = request.scope.get('root_path', '').rstrip('/')
        return get_redoc_html(openapi_url=root + app.openapi_url, title=app.title + ' - ReDoc',
                              redoc_js_url=root + assets + '/redoc.standalone.js',
                              redoc_favicon_url='data:,', with_google_fonts=False)

    app.add_api_route('/docs', swagger, include_in_schema=False)
    app.add_api_route('/redoc', redoc, include_in_schema=False)
    app.add_api_route('/docs/oauth2-redirect', get_swagger_ui_oauth2_redirect_html, include_in_schema=False)
