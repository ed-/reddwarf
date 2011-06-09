vagrant_init_ssh_info() {
     # If the SSH info isn't defined, figure it out here.
    if [ -z "${vcmd_port}" ]
    then
        vcmd_port=`vagrant ssh_config host | grep Port | cut -c 8-`
    fi
    if [ -z "${vcmd_host}" ]
    then
        vcmd_host=`vagrant ssh_config host | grep HostName | cut -c 12-`
    fi
    if [ -z "${vcmd_idfile}" ]
    then
        vcmd_idfile=`vagrant ssh_config host | grep IdentityFile | cut -c 16-`
    fi
    if [ -z "${vcmd_user}" ]
    then
        vcmd_user=`vagrant ssh_config host | grep User | cut -c 8-`
    fi
}

vagrant_clear_ssh_key() {
    # Clears the key so you can SSH to vagrant without the man-in-the-middle warning.
    vagrant_init_ssh_info
    ssh-keygen -R [$vcmd_host]:$vcmd_port
}

vagrant_cmd() {
    # Runs a command via SSH against the vagrant instance.
    vagrant_init_ssh_info
    #ssh vagrant@$vcmd_host -p $vcmd_port -i "$vcmd_idfile" -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no "$@"
    ssh vagrant@$vcmd_host -p $vcmd_port -i "$vcmd_idfile" -o NoHostAuthenticationForLocalhost=yes "$@"
}
