import uuid

from fabric.contrib.files import sed
from fabric.exceptions import NetworkError
from fabric.operations import run, sudo, local, put

from config import SENSOR_HOST_LOCATION
from mhn.tasks import celery

from fabric.decorators import task as fabric_task
from fabric.context_managers import settings as fab_settings, cd
from mhn.api.models import DeployScript, SensorHost, Sensor

RFC_2822 = "%a, %d %b %Y %H:%M:%S +0000"

from mhn import mhn, db

UNCONFIGURED = dict(
    host_string = SENSOR_HOST_LOCATION.format(hostname='honeypie'),
    hostname = 'honeypie',
    user = 'pi',
    password = 'raspberry',
    port = mhn.config['SENSOR_SSH_PORT'],
    ssh_keyfile=mhn.config['SENSOR_KEYS_DIR'] + 'unconfigured_rsa',
    key_filename=mhn.config['SENSOR_KEYS_DIR'] + 'unconfigured_rsa'
)

class FabricException(Exception):
    pass

@celery.task
def configure_sensors():
    """
    search for unconfigured honeypie sensors on the network. if we find one,

    :return:
    """
    @fabric_task
    def configure():
        # start by refreshing the dns on the mhn server machine.
        local("/etc/init.d/dns-clean restart")

        # change the hostname of the sensor: we'll just use a random honeypie hostname.
        try:
            current_hostname = run("hostname")
        except NetworkError, e:
            print "No unconfigured sensors found on the network... nothing to do here."
            return

        if current_hostname != "honeypie":
            # we probably shouldn't be here at this point - if the hostname
            # has already been changed, we shouldn't be accessing this sensor
            return

        # todo: pick good hostnames
        new_hostname = "honeypie-" + uuid.uuid4().hex
        sudo('hostname {}'.format(new_hostname))
        sudo('echo {} > /etc/hostname'.format(new_hostname))
        sed('/etc/hosts', 'honeypie', new_hostname, use_sudo=True)
        sudo('hostname {}'.format(new_hostname))

        # create the new SensorHost object to track it
        host = SensorHost(hostname=new_hostname, status="New")
        db.session.add(host)
        db.session.commit()

        # create a local ssh key to use on the new honeypot.
        local('rm -rf {}'.format(host.keyfile))
        local("ssh-keygen -f {} -q -N \"\"".format(host.keyfile))
        put(host.keyfile + ".pub", '/home/pi')
        sudo("cat /home/pi/{}.pub > .ssh/authorized_keys".format(host.hostname))

        # disable the old key on the sensor host

    with fab_settings(warn_only=True, abort_exception=FabricException, abort_on_prompts=True, **UNCONFIGURED):
        configure()

@celery.task()
def run_installation(script_id, host_id):
    """
    runs an installation script on a host
    :param honeypot_id:
    :param host_id:
    :return:
    """

    @fabric_task
    def install(script, script_file):
        try:
            put(script_file, '/home/pi/deploy.sh', use_sudo=True)
            url = mhn.config.get("SERVER_BASE_URL")
            port = mhn.config.get("SERVER_PORT", None)
            if port:
                url += ":{}".format(port)

            deploy_key = mhn.config.get("DEPLOY_KEY")
            with cd("/home/pi/"):
                result = sudo("bash /home/pi/deploy.sh {url} {deploy_key}".format(url=url, capture=True, deploy_key=deploy_key, id=script.id))
                final_line = result.split('\n')[-1]
                print final_line
                if final_line.lower().startswith("fatal"):
                    raise Exception(final_line)
            return "ok"
        except Exception, e:
            return Exception(e)

    script = DeployScript.query.get(script_id)
    host = SensorHost.query.get(host_id)

    # run the deploy script on the sensor
    filename = '/tmp/{host}_{honeypot}.sh'.format(host=host.hostname, honeypot=script.id)
    f = open(filename, 'w')
    f.write(script.script)
    f.close()

    with fab_settings(warn_only=True, abort_exception=FabricException, abort_on_prompts=True, **host.fab_env):

        result = install(script, filename)

    if isinstance(result, Exception):
        raise result

    # We rely on the installation scripts to handle the registration of the installed sensor with mhn-server,
    # so we don't have access to the id of the sensor that we just installed on the host.

    # We look it up based on three things:
    #   1) it will not have an associated host
    #   2) it will have the script we just used
    #   3) its hostname will be our host's hostname

    sensor = Sensor.query.filter(Sensor.host_id == None).filter(Sensor.hostname == host.hostname)
    # this is a bit brittle, because if we the host's hostname changes during installation,
    # the names might not match.
    if sensor.count() != 1:
        raise Exception("found {} sensor(s), instead of 1. ;(".format(sensor.count()))
    else:
        sensor = sensor[0]

    # associate the sensor with the host
    sensor.host = host
    db.session.commit()

@celery.task
def run_updates():
    sensor_hosts = SensorHost.query.all()

    @fabric_task
    def update(sensor_host):

        try:
            sudo("unattended-upgrade")
            sensor_host.status = "ok"
            sensor_host.exception = None

        except Exception, e:
            sensor_host.status = "error"
            sensor_host.exception = str(e)

    for host in sensor_hosts:
        with fab_settings(warn_only=True, abort_exception=FabricException, abort_on_prompts=True, **host.fab_env):
            update(host)

        db.session.commit()

@celery.task
def run_pings(host_id=None):
    if host_id:
        sensor_hosts = SensorHost.query.filter_by(id=host_id)
    else:
        sensor_hosts = SensorHost.query.all()

    @fabric_task
    def ping(sensor_host):
        """
        check in with important services
        :param sensor_host:
        :return:
        """

        try:
            result = run("hostname")
            sensor_host.status = "ok"
            sensor_host.exception = None
            return result

        except Exception, e:
            sensor_host.status = "lost"
            sensor_host.exception = str(e)

    for host in sensor_hosts:
        with fab_settings(abort_exception=FabricException, abort_on_prompts=True, **host.fab_env):
            ping(host)
        db.session.commit()

