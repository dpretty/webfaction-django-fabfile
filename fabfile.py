# -*- coding: utf-8 -*-
"""
Fabfile template for deploying django apps on webfaction using gunicorn,
and supervisor.

main commands:
fab setup
fab update

# TODO:
 -- currently only for single-app websites.
 -- if an app with our app name already exists, we use it without checking its structure..
 -- we take the first listed IP address. I only have one so it's not an issue for me...
 -- assume user has virtualenv setup how they like it...
 -- would prefer to use django-admin over manage.py, but have had issues before with django-configurations - do they work together yet?
"""

import os.path
import sys
import xmlrpclib

from fabric.api import *
from fabric.contrib.files import upload_template, exists, append


import string, random

try:
    from fabsettings import DOMAIN_NAME, APPLICATION_NAME, WEBSITE_NAME, APPLICATION_PATH, SUBDOMAIN_PREFIX
    from fabsettings import PYTHON_VERSION, SSH_USER, SSH_PASSWORD, SSH_HOST, ENVIRONMENT_VARIABLES
    from fabsettings import CONTROL_PANEL_USER, CONTROL_PANEL_PASSWORD, REPOSITORY
except ImportError:
    print("ImportError: Couldn't find fabsettings.py, it either does not exist or giving import problems (missing settings)")
    sys.exit(1)

env.hosts = [SSH_HOST]
env.website_name = WEBSITE_NAME
env.domain_name = DOMAIN_NAME
env.subdomain_prefix = SUBDOMAIN_PREFIX
env.application_name = APPLICATION_NAME
env.application_path = APPLICATION_PATH
env.python_version = PYTHON_VERSION
env.user = SSH_USER
env.password = SSH_PASSWORD
env.ssh_home = os.path.join('/home', env.user)
env.repository = REPOSITORY
# name of the supervisor virtualenv
env.supervisor_venv = 'supervisor'
env.supervisor_webapp = 'supervisor'
env.template_dir    = 'fabric_templates'
env.environment_variables = ENVIRONMENT_VARIABLES
# relative to django path
env.requirements_file = 'requirements/production.txt'
env.use_migrations = True
env.use_https=False

def get_envvar(varname):
    return run("echo $%s" %varname)

def subdomain_exists(domain_name):
    """Return True if domain name (or subdomain) exists, False if it doesn't"""

    # the API returns subdomain prefixes along with domains - we want a single list of
    # domains and subdomains to check against, so let's create it.

    api_response = list_domains()
    all_domains_and_subdomains = []
    for domain in api_response:
        all_domains_and_subdomains.append(domain['domain'])
        for subdomain in domain:
            all_domains_and_subdomains.append('.'.join([subdomain, domain['domain']]))
    return (domain_name in all_domains_and_subdomains)

def get_website_data(website_name):
    """Get the webfaction website data for website_name."""
    
    api_response = list_websites()
    website_data = None
    for website in api_response:
        if website['name'] == website_name:
            website_data = website
    return website_data

def website_exists(website_name):
    """Check whether webfation website with specified name exists."""

    website_data = get_website_data(website_name)   
    return (website_data is not None)

def process_subdomain(domain_name, subdomain_prefix):
    if subdomain_prefix:
        subdomain = '.'.join([subdomain_prefix, domain_name])
    else:
        subdomain = domain_name
    return subdomain

def check_website_structure(website_name, domain_name, subdomain_prefix, application_name, application_path):
    """raise exception if website doesn't use the specified domain and app."""

    website_data = get_website_data(website_name)
    subdomain = process_subdomain(domain_name, subdomain_prefix)
    if website_data is None:
        raise Exception('Cannot find data for website "%s"' % website_name)
    if subdomain not in website_data['subdomains']:
        raise Exception('Domain name "%s" is not configured for website "%s"' % (domain_name, website_name))
    if [application_name, application_path] not in website_data['website_apps']:
        raise Exception('App ["%s", "%s"] not configured for website "%s"' % (application_name, application_path, website_name))

def get_app_data(app_name):
    """Get the webfaction app data for app_name."""
    
    api_response = list_apps()
    app_data = None
    for app in api_response:
        if app['name'] == app_name:
            app_data = app
    return app_data
    
def app_exists(app_name):
    app_data = get_app_data(app_name)
    return (app_data is not None)
    
    
def setup_website():
    """..."""
    
    if website_exists(env.website_name):
        print("website exists")
        check_website_structure(env.website_name, env.domain_name, env.subdomain_prefix, env.application_name, env.application_path)
    else:
        print("creating website")
        subdomain = process_subdomain(env.domain_name, env.subdomain_prefix)
        if not subdomain_exists(subdomain):
            create_domain(env.domain_name, env.subdomain_prefix)
        if not app_exists(env.application_name):
            create_app(env.application_name, 'custom_app_with_port', False, '', False)
        ip_address = get_webfaction_ip()
        
        create_website(env.website_name, ip_address, env.use_https, [subdomain], [env.application_name, env.application_path])


def get_webfaction_ip():
    ip_list = list_ips()
    return ip_list[0]['ip']

def setup():
    setup_website()
    setup_project_environment()
    install_project()

def list_virtualenvs():
    result = run('lsvirtualenv -b')
    return result.split("\r\n")

def install_supervisor():
    create_virtualenv(env.supervisor_venv, python_version='2.7')
    _ve_run(env.supervisor_venv, "pip install supervisor")
    result = create_app(env.supervisor_webapp, 'custom_app_with_port', False, '', False)
    supervisor_port = result['port']
    # upload supervisor.conf template
    supervisor_dir = os.path.join(env.ssh_home, 'webapps', env.supervisor_webapp)
    upload_template(os.path.join(env.template_dir, 'supervisord.conf'),
                    os.path.join(supervisor_dir, 'supervisord.conf'),
                    {
                        'user':     env.user,
                        'password': env.password,
                        'port': supervisor_port,
                        'dir':  supervisor_dir,
                    },
                    )

    # upload and install crontab
    upload_template(os.path.join(env.template_dir, 'start_supervisor.sh'), 
                    os.path.join(supervisor_dir, 'start_supervisor.sh'),
                      {
                         'user':     env.user,
                         'virtualenv': os.path.join(get_envvar("WORKON_HOME"), env.supervisor_venv)
                     },
                     mode=0750,
                     )

    # add to crontab

    filename = ''.join(random.choice(string.ascii_uppercase + string.digits) for x in range(7))
    run('crontab -l > /tmp/%s' % filename)
    append('/tmp/%s' % filename, '*/10 * * * * %s/start_supervisor.sh start' % supervisor_dir)
    run('crontab /tmp/%s' % filename)


    # create supervisor/conf.d
    with cd(supervisor_dir):
        run('mkdir conf.d')

    with cd(supervisor_dir):
         with settings(warn_only=True):
             run('./start_supervisor.sh stop && ./start_supervisor.sh start')

def reset_virtualenv_environment_variables():
    venv_path = os.path.join(get_envvar("WORKON_HOME"), env.application_name)
    postactivate_path = os.path.join(venv_path, 'bin', 'postactivate')
    predeactivate_path = os.path.join(venv_path, 'bin', 'predeactivate')
    for envvar, value in env.environment_variables.items():
        append(postactivate_path, "export %s=%s" % (envvar, value))
        append(predeactivate_path, "unset %s" % (envvar))

    
def virtualenv_exists(venv_name):
    return (venv_name in list_virtualenvs())

def supervisor_exists():
    return app_exists(env.supervisor_webapp)
    
def setup_project_environment():
    if not supervisor_exists():
        install_supervisor()

#def install_project():
#    pass

def update_project():
    workon_home = get_envvar("WORKON_HOME")
    repo_path = os.path.join(workon_home, env.application_name, env.application_name)
    with cd(repo_path):
        run('git pull')
        _ve_run(env.application_name, 'pip install -r %s' % env.requirements_file)
        _ve_run(env.application_name, "python %s/manage.py syncdb" % env.application_name)
        if env.use_migrations:
            _ve_run(env.application_name, "python %s/manage.py migrate" % env.application_name)
        _ve_run(env.application_name, 'python %s/manage.py collectstatic' % env.application_name)


def update():
    update_project()
    restart_app()

     
def test():
    pass

def get_app_port(app_name):
    app_data = get_app_data(app_name)
    return app_data["port"]


def install_project():
    """Installs the django project in its own wf app and virtualenv
    """

    app_port = get_app_port(env.application_name)
    webapp_dir = os.path.join(env.ssh_home, 'webapps')
    supervisor_dir = os.path.join(webapp_dir, env.supervisor_webapp)
    workon_home = get_envvar("WORKON_HOME")
    venv_dir = os.path.join(workon_home, env.application_name)
    project_dir= os.path.join(venv_dir, env.application_name)
    config_filename = env.application_name+'.conf'
    webapp_dir = os.path.join(webapp_dir, env.application_name)
    start_gunicorn_path = os.path.join(webapp_dir, 'bin', 'start_gunicorn.sh')
    run('mkdir -p %s' % os.path.join(webapp_dir, 'bin'))
        
    upload_template(os.path.join(env.template_dir, 'start_gunicorn.sh'),
                    start_gunicorn_path,
                     {
                        'application_name': env.application_name,
                        'virtualenv_dir': venv_dir,
                        'webapp_dir': webapp_dir,
                        'port': app_port,
                      },
                        mode=0750,
                     )


    # upload template to supervisor conf
    upload_template(os.path.join(env.template_dir, 'gunicorn.conf'),
                    os.path.join(supervisor_dir, 'conf.d', config_filename),
                     {
                         'project': env.application_name,
                         'start_gunicorn_path': start_gunicorn_path,
                         'project_dir': project_dir,
                         'virtualenv': venv_dir,
                         'user': env.user,
                      }
                     )

    if not virtualenv_exists(env.application_name):
        create_virtualenv(env.application_name, environment_variables=env.environment_variables)
    
    if not exists(os.path.join(workon_home, env.application_name, env.application_name, '.git')):
        with cd(os.path.join(workon_home, env.application_name)):
            run('git clone %s %s' % (env.repository, env.application_name))

    update_project()
    restart_app()

def restart_app():
    """Restarts the app using supervisorctl"""
    supervisor_dir = os.path.join(env.ssh_home, 'webapps', env.supervisor_webapp)
    config_file = os.path.join(supervisor_dir, 'supervisord.conf')
    #with cd(env.supervisor_dir):
    _ve_run(env.supervisor_venv,'supervisorctl -c %s reread && supervisorctl -c %s reload' %(config_file, config_file))
    _ve_run(env.supervisor_venv,'supervisorctl -c %s restart %s' % (config_file, env.application_name))

### Webfaction API

def list_domains():
    """Get information about the account’s domains.
    
    returns list of dicts containing:
    id: domain ID
    domain: domain name
    subdomains: list of subdomains for the domain

    e.g.:
    [{'domain':'example.com', id:12345, subdomains:['www','media']}, { ... }, { ... }]
    """

    return _webfaction_api_call("list_domains")

def list_websites():
    """Get information about the account’s websites.

    returns list of dicts containing:
    id: website ID
    name: website name
    ip: website IP address
    https: whether the website is served over HTTPS
    subdomains: array of website’s subdomains
    website_apps: array of the website’s apps and their URL paths;
                  each item in the array is a two-item array,
                  containing an application name and URL path.
    """

    return _webfaction_api_call("list_websites")

def create_domain(domain_name, subdomain_prefix):
    """Create a domain entry

    Parameters:
    domain (string) – a domain name in the form of example.com
    subdomain (string) – each additional parameter provided after domain: a subdomain name of domain
    
    If  domain has  already been  created, you  may supply  additional
    parameters to add subdomains.  For example, if example.com already
    exists,  create_domain  may  be  called with  four  parameters—  a
    session  ID, example.com,  www, private—to  create www.example.com
    and private.example.com.
    """
    
    return _webfaction_api_call("create_domain", domain_name, subdomain_prefix)

def list_apps():
    """Get information about the account’s applications.

    The method returns a list of dicts with the following key-value pairs:

    id: app ID
    name: app name
    type: app type
    autostart: whether the app uses autostart
    port: port number if the app listens on a port, otherwise is 0
    open_port: for applications that listen on a port, whether the port is open on shared and dedicated IP addresses (True for open ports, False for closed ports, or for applications that do not listen to a port)
    extra_info: extra info for the app if any
    machine: name of the machine where the app resides
    """

    return _webfaction_api_call("list_apps")


def create_app(app_name, app_type, autostart, extra_info, open_port):
    """Create a new application.

    Parameters:
    session_id – session ID returned by login
    name (string) – name of the application
    type (string) – type of the application
    autostart (boolean) – whether the app should restart with an autostart.cgi script (optional, default: false)
    extra_info (string) – additional information required by the application; if extra_info is not required or used by the application, it is ignored (optional, default: an empty string)
    open_port (boolean) – for applications that listen on a port, whether the port should be open on shared and dedicated IP addresses (optional, default: false)
    """

    return _webfaction_api_call("create_app", app_name, app_type, autostart, extra_info, open_port)


def create_website(website_name, ip, https, subdomains, site_apps):
    """Create a new website entry.

    Applications may  be added  to the  website entry  with additional
    parameters  supplied after  subdomains. The  additional parameters
    must be arrays  containing two elements: a  valid application name
    and a path (for example, 'htdocs' and '/').
        
    Parameters:
    website_name (string): the name of the new website entry
    ip (string): IP address of the server where the entry resides
    https (boolean): whether the website entry should use a secure connection
    subdomains (array): an array of strings of (sub)domains to be associated with the website entry
    site_apps (array): each additional parameter provided after subdomains: an array containing a valid application name (a string) and a URL path (a string)
    """

    return _webfaction_api_call("create_website", website_name, ip, https, subdomains, site_apps)
    

def list_ips():
    """Get information about all of the account’s machines and their IP addresses.

    This method returns an array of structs with the following key-value pairs:

    machine: machine name (for example, Web100)
    ip: IP address
    is_main: a boolean value indicating whether the IP address is the primary address for the server (true) or an extra IP address provisioned to the account (false)
    """

    return _webfaction_api_call("list_ips")

### Helper functions


def create_virtualenv(venv_name, python_version=env.python_version, environment_variables={}):
    run('mkvirtualenv -p /usr/local/bin/python%s --no-site-packages %s' %(python_version, venv_name))
    venv_path = os.path.join(get_envvar("WORKON_HOME"), venv_name)
    postactivate_path = os.path.join(venv_path, 'bin', 'postactivate')
    predeactivate_path = os.path.join(venv_path, 'bin', 'predeactivate')
    for envvar, value in environment_variables.items():
        append(postactivate_path, "export %s=%s" % (envvar, value))
        append(predeactivate_path, "unset %s" % (envvar))
        
        
            

def _ve_run(ve,cmd):
    """virtualenv wrapper for fabric commands
    """
    workon_home = get_envvar("WORKON_HOME")
    activate_path = os.path.join(workon_home, ve, 'bin', 'activate')
    postactivate_path = os.path.join(workon_home, ve, 'bin', 'postactivate')
    run("""source %s && source %s && %s""" % (activate_path, postactivate_path, cmd))

def _webfaction_api_call(api_command, *args):
    """Wrapper which handles XML-RPC connection for webfaction API
    """
    server = xmlrpclib.ServerProxy('https://api.webfaction.com/')
    session_id, account = server.login(CONTROL_PANEL_USER, CONTROL_PANEL_PASSWORD)
    api_args = (session_id, ) + args
    response = getattr(server, api_command)(*api_args)
    return response

  
