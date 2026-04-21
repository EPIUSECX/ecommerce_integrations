<div align="center">
    <img src="https://frappecloud.com/files/ERPNext%20-%20Ecommerce%20Integrations.png" height="128">
    <h2>Ecommerce Integrations for ERPNext</h2>

[![CI](https://github.com/frappe/ecommerce_integrations/actions/workflows/ci.yml/badge.svg)](https://github.com/frappe/ecommerce_integrations/actions/workflows/ci.yml)
  
</div>

### Currently supported integrations:

- Shopify - [User documentation](https://docs.erpnext.com/docs/v13/user/manual/en/erpnext_integration/shopify_integration)
- Unicommerce - [User Documentation](https://docs.erpnext.com/docs/v13/user/manual/en/erpnext_integration/unicommerce_integration)
- Zenoti - [User documentation](https://docs.erpnext.com/docs/v13/user/manual/en/erpnext_integration/zenoti_integration)
- Amazon - [User documentation](https://docs.erpnext.com/docs/v13/user/manual/en/erpnext_integration/amazon_integration)


### Installation

- Frappe Cloud Users can install [from Marketplace](https://frappecloud.com/marketplace/apps/ecommerce_integrations).
- Self Hosted users can install using Bench:

```bash
# Production installation
$ bench get-app ecommerce_integrations --branch main

# OR development install
$ bench get-app ecommerce_integrations  --branch develop

# install on site
$ bench --site sitename install-app ecommerce_integrations
```

After installation follow user documentation for each integration to set it up.

### Shopify OAuth (authorization code grant)

You can connect **Shopify Setting** using **OAuth** instead of pasting an Admin API access token:

1. Configure the Shopify app as a **non-embedded app**, or use an app version with **legacy install flow** enabled. This connector doesn't implement Shopify managed installation / token exchange.
2. In the **Partner Dashboard** (or `shopify.app.toml`), set **`[auth].redirect_urls`** to exactly:
   `https://<your-site>/api/method/ecommerce_integrations.shopify.oauth.shopify_oauth_callback`
3. Set **`[access_scopes].scopes`** to match (or be a superset of) the scopes in `SHOPIFY_OAUTH_SCOPES` in [`ecommerce_integrations/shopify/constants.py`](ecommerce_integrations/shopify/constants.py).
4. In ERPNext **Shopify Setting**: enable Shopify, set **Authentication method** to **OAuth**, enter the store's permanent **`<shop>.myshopify.com`** URL, **Client ID**, and **API Secret** (same secret used for webhook HMAC), then click **Connect with Shopify** and approve the app.
5. **Offline** Admin tokens do not require a periodic refresh job; if Shopify returns **401**, status becomes **Needs Reconnection**—update the token or run **Connect with Shopify** again.
6. **Manual** authentication (paste access token) remains supported for store **Develop apps** or legacy setups.

### Contributing

- Follow general [ERPNext contribution guideline](https://github.com/frappe/erpnext/wiki/Contribution-Guidelines)
- Send PRs to `develop` branch only.

### Development setup

- Enable developer mode.
- If you want to use a tunnel for local development. Set `localtunnel_url` parameter in your site_config file with ngrok / localtunnel URL. This will be used in most places to register webhooks. Likewise, use this parameter wherever you're sending current site URL to integrations in development mode.


#### License

GNU GPL v3.0
