{ config, pkgs, lib, settingsFormat }:
with lib;
let
  # The directory that contains the state directory for each service /
  # Tahoe-LAFS node.
  stateRoot = "/var/db/tahoe-lafs";

  forEach = flip mapAttrs';

  # Construct a system username for a certain service.
  # str -> str -> str
  userName = kind: name: "tahoe.${kind}-${name}";

  # Construct a system groupname for a certain service.
  # str -> str -> str
  groupName = userName;

  # Construct the absolute state directory path for a certain service.
  #
  # This is a directory but it has no trailing slash. Tahoe commands get antsy
  # when there's a trailing slash.
  #
  # str -> str -> str
  nodeDir = kind: name: "${stateRoot}/${kind}-${name}";

  # Construct the serviceConfig section for a certain systemd service running
  # a Tahoe-LAFS node.
  #
  # The result is used as the value of `systemd.services.<name>.serviceConfig`.
  #
  # package -> str -> str -> attrset
  mkServiceConfig = package: kind: name: configPath: rec {
    Type = "simple";

    # Get a dedicated directory for runtime state.
    RuntimeDirectory = "tahoe-lafs";

    # Tell systemd where we intend to put our PID file, although as a "simple"
    # service the PID file doesn't do much.  Still, tahoe is going to write
    # one somewhere, systemd might as well know about it.
    PIDFile = "${RuntimeDirectory}/${kind}-${name}.pid";

    # Run unprivileged with the dedicated user and group.
    User = userName kind name;
    Group = groupName kind name;

    # Create the node before starting, if necessary.
    ExecStartPre = mkPreStart package kind name configPath;

    # Run the node process.
    #
    # Note - `tahoe` interprets arguments before the node directory as meant
    # for itself and options after the node directory as meant for the Twisted
    # process runner.
    #
    # The command is written to a file so the PIDFILE environment variable
    # will be resolved.
    ExecStart = let
      nodedir = lib.escapeShellArg (nodeDir kind name);
    in pkgs.writeShellScript "tahoe.${kind}-${name}-start" ''
      ${package}/bin/tahoe run ${nodedir} --pidfile="$PIDFILE"
    '';
  };

  # Construct the preStart script for a certain systemd service running a
  # Tahoe-LAFS node.  This makes sure the state directory exists, initializes
  # Tahoe-LAFS state in it if necessary, and puts the generated configuration
  # file in place.
  #
  # The result is used as the value of `systemd.services.<name>.preStart`.
  #
  # str -> str -> str -> attrset
  mkPreStart = package: kind: name: configPath:
    let
      nodedir = nodeDir kind name;
      nodedirQuoted = lib.escapeShellArg nodedir;

      # Create the state root and a state directory owned by the right user.
      makeStateDirectory = ''
        set -e
        if [ ! -d ${nodedirQuoted} ]; then
          mkdir -p ${lib.escapeShellArg stateRoot}
          mkdir ${nodedirQuoted}
          chown ${userName kind name}:${groupName kind name} ${nodedirQuoted}
        fi
      '';
      # Create the Tahoe-LAFS node state, if it is missing.  We detect
      # "missing" by looking for the configuration file which `create-node`
      # and `create-introducer` will both write and which we will also copy
      # into place at the end of this step.  The directory itself exists
      # already because it was created by the pre-script that runs earlier.
      makeNodeDirectory = ''
        set -e
        if [ ! -e ${nodedirQuoted}/tahoe.cfg ]; then
          ${package}/bin/tahoe create-${kind} \
            --hostname="${config.networking.hostName}" \
            ${lib.escapeShellArg nodedir}
        fi

        # Tahoe has created a predefined tahoe.cfg which we must now scribble
        # over.
        cp ${configPath} ${lib.escapeShellArg nodedir}/tahoe.cfg
      '';
      writeScript = pkgs.writeShellScript "tahoe.${kind}-${name}-pre-start";
    in
      [
        # Keep root privileges so we can create the state root
        "+${writeScript makeStateDirectory}"
        # Don't keep them for running Tahoe to create the node though
        "${writeScript makeNodeDirectory}"
      ];

in
rec {
  # Generate the ini-format Tahoe-LAFS `tahoe.cfg` configuration file from the
  # NixOS service configuration value.
  #
  # The attrset argument is the top of a single node's configuration - for
  # example, `config.services.nodes.bar_node`.
  #
  # The result is the path of the generated configuration file.
  #
  # str -> str -> attrset -> Path
  mkConfig = kind: name: cfg:
    let
      configPathFragment = "tahoe-lafs/${kind}-${name}.cfg";
    in
      settingsFormat.generate configPathFragment cfg.settings;

  # Construct the NixOS systemd service configuration for all of the given
  # node configurations.
  #
  # The result is used as the value of `systemd.services`.
  #
  # str -> attrset -> attrset
  mkServices = kind: nodes: forEach nodes (name: cfg:
    nameValuePair "tahoe.${kind}-${name}" {
      description = "Tahoe LAFS node ${name}";
      wantedBy = [ "multi-user.target" ];
      serviceConfig = mkServiceConfig cfg.package kind name (mkConfig kind name cfg);
    }
  );

  # Construct the NixOS static user configuration for all of the given node
  # configurations.
  #
  # The result is used as the value of `users.users`.
  #
  # str -> str -> attrset
  mkUsers = kind: nodes: forEach nodes (name: cfg:
    nameValuePair (userName kind name) {
      description = "Tahoe node user for ${kind} ${name}";
      isSystemUser = true;
      group = groupName kind name;
    }
  );

  # Like mkUsers, but for group configuration.
  mkGroups = kind: nodes: forEach nodes (name: cfg:
    nameValuePair (groupName kind name) { }
  );
}
