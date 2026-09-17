#!/usr/local/CyberCP/bin/python
import os
import sys
import time

sys.path.append('/usr/local/CyberCP')
import plogical.CyberCPLogFileWriter as logging
from serverStatus.serverStatusUtil import ServerStatusUtil
from plogical.processUtilities import ProcessUtilities

class DockerInstall:
    @staticmethod
    def submitInstallDocker(CommandCP=0):
        try:
            statusFile = open(ServerStatusUtil.lswsInstallStatusPath, 'w')

            logging.CyberCPLogFileWriter.statusWriter(ServerStatusUtil.lswsInstallStatusPath,
                                                      "Starting Docker Installation..\n", 1)

            # Check if Podman is installed  
            if os.system("which podman > /dev/null 2>&1") == 0:  #  
                logging.CyberCPLogFileWriter.statusWriter(ServerStatusUtil.lswsInstallStatusPath,
                                                          "Podman detected. Removing Podman before installing Docker...\n", 1)  #  

                # Stop and remove all Podman containers (NEW LINES)
                ServerStatusUtil.executioner("podman stop -a", statusFile)  #  
                ServerStatusUtil.executioner("podman rm -a", statusFile)  #  

                # Remove Podman completely  
                if ProcessUtilities.decideDistro() in [ProcessUtilities.cent8, ProcessUtilities.centos]:  #  
                    ServerStatusUtil.executioner("dnf remove -y podman", statusFile)  #  
                else:  # Debian-based  
                    ServerStatusUtil.executioner("DEBIAN_FRONTEND=noninteractive apt-get remove -y podman", statusFile)  #

                # Remove leftover Podman directories (NEW LINES)
                ServerStatusUtil.executioner("rm -rf /var/lib/containers", statusFile)  #  

                # Remove Podman socket if it exists (NEW LINES)
                if os.path.exists("/run/podman/podman.sock"):  #  
                    os.remove("/run/podman/podman.sock")  #  
                    logging.CyberCPLogFileWriter.statusWriter(ServerStatusUtil.lswsInstallStatusPath,
                                                              "Removed Podman socket.\n", 1)  #  

                logging.CyberCPLogFileWriter.statusWriter(ServerStatusUtil.lswsInstallStatusPath,
                                                          "Podman completely removed.\n", 1)  #  

            # Check and unset DOCKER_HOST if set (NEW LINES)
            if "DOCKER_HOST" in os.environ:  #  
                ServerStatusUtil.executioner("unset DOCKER_HOST", statusFile)  #  
                logging.CyberCPLogFileWriter.statusWriter(ServerStatusUtil.lswsInstallStatusPath,
                                                          "Unset DOCKER_HOST to avoid conflicts.\n", 1)  #  

            # Remove systemd override if it forces Podman for Docker (NEW LINES)
            if os.path.exists("/etc/systemd/system/docker.service.d/override.conf"):  #  
                os.remove("/etc/systemd/system/docker.service.d/override.conf")  #  
                logging.CyberCPLogFileWriter.statusWriter(ServerStatusUtil.lswsInstallStatusPath,
                                                          "Removed systemd override forcing Podman.\n", 1)  #  

            # Install Docker based on OS version
            if ProcessUtilities.decideDistro() == ProcessUtilities.cent8:
                if os.path.exists(ProcessUtilities.debugPath):
                    logging.CyberCPLogFileWriter.writeToFile(f'Docker installation started for cent8/9')

                commands = [
                    'sudo yum install -y yum-utils',
                    'yum install yum-utils -y',
                    'yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo',
                    'sudo dnf install docker-ce docker-ce-cli containerd.io docker-compose-plugin --allowerasing -y'
                ]
            elif ProcessUtilities.decideDistro() == ProcessUtilities.centos:
                commands = ['sudo yum install -y docker']
            else:
                commands = ['DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose']

            for command in commands:
                if CommandCP:
                    ProcessUtilities.executioner(command, 'root', True)
                else:
                    if not ServerStatusUtil.executioner(command, statusFile):
                        logging.CyberCPLogFileWriter.statusWriter(ServerStatusUtil.lswsInstallStatusPath,
                                                                  "Failed to install Docker. [404]\n", 1)
                        return 0

            # Enable and start Docker service
            ProcessUtilities.executioner('sudo systemctl enable docker', 'root', True)
            ProcessUtilities.executioner('sudo systemctl start docker', 'root', True)

            logging.CyberCPLogFileWriter.statusWriter(ServerStatusUtil.lswsInstallStatusPath,
                                                      "Docker successfully installed. [200]\n", 1)

            time.sleep(2)

        except BaseException as msg:
            logging.CyberCPLogFileWriter.statusWriter(ServerStatusUtil.lswsInstallStatusPath, str(msg) + ' [404].', 1)

def main():
    DockerInstall.submitInstallDocker()

if __name__ == "__main__":
    main()