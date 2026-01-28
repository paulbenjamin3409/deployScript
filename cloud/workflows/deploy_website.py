# cloud/workflows/deploy_website.py
def deploy_website(provider, config):

    provider.ensure_resources()
    
    provider.deploy_app()

