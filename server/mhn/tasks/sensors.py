from fabric.contrib.files import sed
from fabric.exceptions import NetworkError
from fabric.operations import run, sudo, local, put

from mhn.tasks import celery
from fabric.decorators import task as fabric_task
from fabric.context_managers import settings as fab_settings, cd
from mhn.api.models import DeployScript, SensorHost

RFC_2822 = "%a, %d %b %Y %H:%M:%S +0000"

from mhn import mhn, db

UNCONFIGURED = dict(
    host_string = 'honeypie.local',
    hostname = 'honeypie.local',
    user = 'pi',
    password = 'raspberry',
    port = mhn.config['SENSOR_SSH_PORT'],
    ssh_keyfile=mhn.config['SENSOR_KEYS_DIR'] + 'unconfigured'
)

@celery.task
def configure_sensors():
    """
    search for unconfigured honeypie sensors on the network. if we find one,

    :return:
    """
    @fabric_task
    def configure():
        # start by refreshing the dns on the mhn server machine.
        local("sudo /etc/init.d/dns-clean restart")

        # change the hostname of the sensor: we'll just use a random honeypie hostname.
        try:
            current_hostname = run("hostname")
        except NetworkError, e:
            print "No unconfigured sensors found on the network... nothing to do here."
            return

        if current_hostname == "honeypie":
            # todo: pick good hostnames
            new_hostname = "honeypie" # + uuid.uuid4().hex
            sudo('hostname {}'.format(new_hostname))
            sudo('echo {} > /etc/hostname'.format(new_hostname))
            sed('/etc/hosts', 'honeypie', new_hostname, use_sudo=True)
            sudo('hostname {}'.format(new_hostname))

        else:
            # we probably shouldn't be here at this point - if the hostname
            # has already been changed, we shouldn't be accessing this sensor
            new_hostname = current_hostname


        # create the new SensorHost object to track it
        host = SensorHost(hostname=new_hostname, status="New")
        db.session.add(host)
        db.session.commit()

        # create a local ssh key to use on the new honeypot.
        local('rm -rf {}'.format(host.keyfile))
        local("ssh-keygen -f {} -q -N \"\"".format(host.keyfile))
        put(host.keyfile + ".pub", '/home/pi')
        sudo("cat /home/pi/{}.pub > authorized_keys".format(host.hostname))

        # disable the old key on the sensor host

    with fab_settings(**UNCONFIGURED):
        configure()

@celery.task
def run_installation(script_id, host_id):
    """
    runs an installation script on a host
    :param honeypot_id:
    :param host_id:
    :return:
    """
    script = DeployScript.query.get(id=script_id)
    host = SensorHost.query.get(id=host_id)

    # run the deploy script on the sensor
    filename = '/tmp/{host}_{honeypot}.sh'.format(host=host.hostname, honeypot=script.id)
    f = open(filename, 'w')
    f.write(script.script)
    f.close()

    @fabric_task
    def install(script, script_file):
        put(script_file, '/home/pi/deploy.sh', use_sudo=True)
        url = mhn.config.get("SERVER_BASE_URL")
        deploy_key = mhn.config.get("DEPLOY_KEY")
        with cd("/home/pi/"):
            run("bash /tmp/deploy.sh {url} {deploy_key}".format(url=url, deploy_key=deploy_key, id=script.id))

    with fab_settings(**host.fab_env):
        install(script, filename)

@celery.task
def run_updates():
    sensor_hosts = SensorHost.query.all()

    @fabric_task
    def update(sensor_host):

        try:
            sudo("DEBIAN_FRONTEND=noninteractive apt-get update")
            sudo("DEBIAN_FRONTEND=noninteractive apt-get upgrade")
            sensor_host.status = "ok"
            sensor_host.exception = None

        except Exception, e:
            sensor_host.status = "error"
            sensor_host.exception = e

        db.session.commit()

    for host in sensor_hosts:
        with fab_settings(**host.fab_env):
            update(host)

@celery.task
def run_pings():
    sensor_hosts = SensorHost.query.all()

    @fabric_task
    def checkup(sensor_host):
        """
        check in with important services
        :param sensor_host:
        :return:
        """

        try:
            result = run("echo hostname")
            sensor_host.status = "ok"

        except Exception, e:
            sensor_host.status = "lost"
            result = e

        db.session.commit()
        return result

    for host in sensor_hosts:
        with fab_settings(**host.fab_env):
            checkup(host)
