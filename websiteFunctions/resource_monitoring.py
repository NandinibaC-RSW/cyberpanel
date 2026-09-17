import psutil
import os
from plogical.processUtilities import ProcessUtilities
from plogical.acl import ACLManager
import plogical.CyberCPLogFileWriter as logging

def get_website_resource_usage(externalApp):
    try:
        user = externalApp
        if not user:
            return {'status': 0, 'error_message': 'User not found'}

        # Get CPU and Memory usage using ps command
        command = f"ps -u {user} -o pcpu,pmem | grep -v CPU | awk '{{cpu += $1; mem += $2}} END {{print cpu, mem}}'"
        result = ProcessUtilities.outputExecutioner(command)
        
        try:
            cpu_percent, memory_percent = map(float, result.split())
        except:
            cpu_percent = 0
            memory_percent = 0

        # Get disk usage using du command
        website_path = f"/home/{user}/public_html"
        if os.path.exists(website_path):
            # Get disk usage in MB
            command = f"du -sm {website_path} | cut -f1"
            disk_used = float(ProcessUtilities.outputExecutioner(command))
            
            # Get total disk space
            command = f"df -m {website_path} | tail -1 | awk '{{print $2}}'"
            disk_total = float(ProcessUtilities.outputExecutioner(command))
            
            # Calculate percentage
            disk_percent = (disk_used / disk_total) * 100 if disk_total > 0 else 0
        else:
            disk_used = 0
            disk_total = 0
            disk_percent = 0

        return {
            'status': 1,
            'cpu_usage': round(cpu_percent, 2),
            'memory_usage': round(memory_percent, 2),
            'disk_used': round(disk_used, 2),
            'disk_total': round(disk_total, 2),
            'disk_percent': round(disk_percent, 2)
        }

    except BaseException as msg:
        logging.CyberCPLogFileWriter.writeToFile(f'Error in get_website_resource_usage: {str(msg)}')
        return {'status': 0, 'error_message': str(msg)} 