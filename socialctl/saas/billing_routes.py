"""Account billing actions use the verified browser identity, never body IDs."""
import asyncio
from fastapi import Request
from fastapi.responses import RedirectResponse
from socialctl.saas.routes import read_form


def install_billing_routes(app,billing):
    app.state.saas_billing=billing

    async def action(request,method):
        data=await read_form(request,billing.origin)
        app.state.saas_valid_form(request,data,'saas-account')
        user=await asyncio.to_thread(app.state.saas_sessions.require_user,request)
        url=await asyncio.to_thread(method,user.id)
        response=RedirectResponse(url,status_code=303)
        # These POSTs only redirect to validated Stripe HTTPS destinations.
        response.headers['Content-Security-Policy']="default-src 'none'; form-action 'self' https://checkout.stripe.com https://billing.stripe.com; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.post('/account/billing/checkout')
    async def checkout(request:Request):
        return await action(request,billing.checkout)

    @app.post('/account/billing/portal')
    async def portal(request:Request):
        return await action(request,billing.portal)
