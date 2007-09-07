
from nevow import inevow, loaders, rend, tags as T
from twisted.python import util
import math
from allmydata.util import mathutil

def getxmlfile(name):
    return loaders.xmlfile(util.sibpath(__file__, "web/%s" % name))

# factorial and binomial copied from
# http://mail.python.org/pipermail/python-list/2007-April/435718.html

def factorial(n):
    """factorial(n): return the factorial of the integer n.
    factorial(0) = 1
    factorial(n) with n<0 is -factorial(abs(n))
    """
    result = 1
    for i in xrange(1, abs(n)+1):
        result *= i
    if n >= 0:
        return result
    else:
        return -result

def binomial(n, k):
    if not 0 <= k <= n:
        return 0
    if k == 0 or k == n:
        return 1
    # calculate n!/k! as one product, avoiding factors that
    # just get canceled
    P = k+1
    for i in xrange(k+2, n+1):
        P *= i
    # if you are paranoid:
    # C, rem = divmod(P, factorial(n-k))
    # assert rem == 0
    # return C
    return P//factorial(n-k)

class ProvisioningTool(rend.Page):
    addSlash = True
    docFactory = getxmlfile("provisioning.xhtml")

    def render_forms(self, ctx, data):
        req = inevow.IRequest(ctx)

        def getarg(name, astype=int):
            if req.method != "POST":
                return None
            if name in req.fields:
                return astype(req.fields[name].value)
            return None
        return self.do_forms(getarg)


    def do_forms(self, getarg):
        filled = getarg("filled", bool)

        def get_and_set(name, options, default=None, astype=int):
            current_value = getarg(name, astype)
            i_select = T.select(name=name)
            for (count, description) in options:
                count = astype(count)
                selected = False
                if ((current_value is not None and count == current_value) or
                    (current_value is None and count == default)):
                    o = T.option(value=str(count), selected="true")[description]
                else:
                    o = T.option(value=str(count))[description]
                i_select = i_select[o]
            if current_value is None:
                current_value = default
            return current_value, i_select

        sections = {}
        def add_input(section, text, entry):
            if section not in sections:
                sections[section] = []
            sections[section].append(T.div[text, ": ", entry])

        def add_output(section, entry):
            if section not in sections:
                sections[section] = []
            sections[section].append(T.div[entry])

        def build_section(section):
            return T.fieldset[T.legend[section], sections[section]]

        def number(value, suffix=""):
            scaling = 1
            if value < 1:
                fmt = "%1.2g%s"
            elif value < 100:
                fmt = "%.1f%s"
            elif value < 1000:
                fmt = "%d%s"
            elif value < 1e6:
                fmt = "%.2fk%s"; scaling = 1e3
            elif value < 1e9:
                fmt = "%.2fM%s"; scaling = 1e6
            elif value < 1e12:
                fmt = "%.2fG%s"; scaling = 1e9
            elif value < 1e15:
                fmt = "%.2fT%s"; scaling = 1e12
            elif value < 1e18:
                fmt = "%.2fP%s"; scaling = 1e15
            else:
                fmt = "huge! %g%s"
            return fmt % (value / scaling, suffix)

        user_counts = [(5, "5 users"),
                       (50, "50 users"),
                       (200, "200 users"),
                       (1000, "1k users"),
                       (10000, "10k users"),
                       (50000, "50k users"),
                       (100000, "100k users"),
                       (500000, "500k users"),
                       (1000000, "1M users"),
                       ]
        num_users, i_num_users = get_and_set("num_users", user_counts, 50000)
        add_input("Users",
                  "How many users are on this network?", i_num_users)

        files_per_user_counts = [(100, "100 files"),
                                 (1000, "1k files"),
                                 (10000, "10k files"),
                                 (100000, "100k files"),
                                 (1e6, "1M files"),
                                 ]
        files_per_user, i_files_per_user = get_and_set("files_per_user",
                                                       files_per_user_counts,
                                                       1000)
        add_input("Users",
                  "How many files in each user's vdrive? (avg)",
                  i_files_per_user)

        space_per_user_sizes = [(1e6, "1MB"),
                                (10e6, "10MB"),
                                (100e6, "100MB"),
                                (1e9, "1GB"),
                                (2e9, "2GB"),
                                (5e9, "5GB"),
                                (10e9, "10GB"),
                                (100e9, "100GB"),
                                (1e12, "1TB"),
                                ]
        space_per_user, i_space_per_user = get_and_set("space_per_user",
                                                       space_per_user_sizes,
                                                       1e9)
        add_input("Users",
                  "How much data is in each user's vdrive? (avg)",
                  i_space_per_user)

        sharing_ratios = [(1.0, "1.0x"),
                          (1.1, "1.1x"),
                          (2.0, "2.0x"),
                          ]
        sharing_ratio, i_sharing_ratio = get_and_set("sharing_ratio",
                                                     sharing_ratios, 1.0,
                                                     float)
        add_input("Users",
                  "What is the sharing ratio? (1.0x is no-sharing and"
                  " no convergence)", i_sharing_ratio)

        # Encoding parameters
        encoding_choices = [("3-of-10-5", "3.3x (3-of-10, repair below 5)"),
                            ("3-of-10-8", "3.3x (3-of-10, repair below 8)"),
                            ("5-of-10-7", "2x (5-of-10, repair below 7)"),
                            ("8-of-10-9", "1.25x (8-of-10, repair below 9)"),
                            ("25-of-100-50", "4x (25-of-100, repair below 50)"),
                            ]
        encoding_parameters, i_encoding_parameters = \
                             get_and_set("encoding_parameters",
                                         encoding_choices, "3-of-10-5", str)
        encoding_pieces = encoding_parameters.split("-")
        k = int(encoding_pieces[0])
        assert encoding_pieces[1] == "of"
        n = int(encoding_pieces[2])
        # we repair the file when the number of available shares drops below
        # this value
        repair_threshold = int(encoding_pieces[3])

        add_input("Servers",
                  "What are the default encoding parameters?",
                  i_encoding_parameters)

        # Server info
        num_server_choices = [ (5, "5 servers"),
                               (10, "10 servers"),
                               (30, "30 servers"),
                               (50, "50 servers"),
                               (100, "100 servers"),
                               (1000, "1k servers"),
                               (10e3, "10k servers"),
                               (100e3, "100k servers"),
                               (1e6, "1M servers"),
                               ]
        num_servers, i_num_servers = \
                     get_and_set("num_servers", num_server_choices, 30, int)
        add_input("Servers",
                  "How many servers are there?", i_num_servers)

        # availability is measured in dBA = -dBF, where 0dBF is 100% failure,
        # 10dBF is 10% failure, 20dBF is 1% failure, etc
        server_dBA_choices = [ (20, "99% [20dBA] (14min/day or 3.5days/year)"),
                               (30, "99.9% [30dBA] (87sec/day or 9hours/year)"),
                               (40, "99.99% [40dBA] (60sec/week or 53min/year)"),
                               (50, "99.999% [50dBA] (5min per year)"),
                               ]
        server_dBA, i_server_availability = \
                    get_and_set("server_availability",
                                server_dBA_choices,
                                20, int)
        add_input("Servers",
                  "What is the server availability?", i_server_availability)

        drive_MTBF_choices = [ (40, "40,000 Hours"),
                               ]
        drive_MTBF, i_drive_MTBF = \
                    get_and_set("drive_MTBF", drive_MTBF_choices, 40, int)
        add_input("Drives",
                  "What is the hard drive MTBF?", i_drive_MTBF)
        # http://www.tgdaily.com/content/view/30990/113/
        # http://labs.google.com/papers/disk_failures.pdf
        # google sees:
        #  1.7% of the drives they replaced were 0-1 years old
        #  8% of the drives they repalced were 1-2 years old
        #  8.6% were 2-3 years old
        #  6% were 3-4 years old, about 8% were 4-5 years old

        drive_size_choices = [ (100, "100 GB"),
                               (250, "250 GB"),
                               (500, "500 GB"),
                               (750, "750 GB"),
                               ]
        drive_size, i_drive_size = \
                    get_and_set("drive_size", drive_size_choices, 750, int)
        drive_size = drive_size * 1e9
        add_input("Drives",
                  "What is the capacity of each hard drive?", i_drive_size)
        drive_failure_model_choices = [ ("E", "Exponential"),
                                        ("U", "Uniform"),
                                        ]
        drive_failure_model, i_drive_failure_model = \
                             get_and_set("drive_failure_model",
                                         drive_failure_model_choices,
                                         "E", str)
        add_input("Drives",
                  "How should we model drive failures?", i_drive_failure_model)

        # drive_failure_rate is in failures per second
        if drive_failure_model == "E":
            drive_failure_rate = 1.0 / (drive_MTBF * 1000 * 3600)
        else:
            drive_failure_rate = 0.5 / (drive_MTBF * 1000 * 3600)

        # deletion/gc/ownership mode
        ownership_choices = [ ("A", "no deletion, no gc, no owners"),
                              ("B", "deletion, no gc, no owners"),
                              ("C", "deletion, share timers, no owners"),
                              ("D", "deletion, no gc, yes owners"),
                              ("E", "deletion, owner timers"),
                              ]
        ownership_mode, i_ownership_mode = \
                        get_and_set("ownership_mode", ownership_choices,
                                    "A", str)
        add_input("Servers",
                  "What is the ownership mode?", i_ownership_mode)

        # client access behavior
        access_rates = [ (1, "one file per day"),
                         (10, "10 files per day"),
                         (100, "100 files per day"),
                         (1000, "1k files per day"),
                         (10e3, "10k files per day"),
                         (100e3, "100k files per day"),
                         ]
        download_files_per_day, i_download_rate = \
                                get_and_set("download_rate", access_rates,
                                            100, int)
        add_input("Users",
                  "How many files are downloaded per day?", i_download_rate)
        download_rate = 1.0 * download_files_per_day / (24*60*60)

        upload_files_per_day, i_upload_rate = \
                              get_and_set("upload_rate", access_rates,
                                          10, int)
        add_input("Users",
                  "How many files are uploaded per day?", i_upload_rate)
        upload_rate = 1.0 * upload_files_per_day / (24*60*60)

        delete_files_per_day, i_delete_rate = \
                              get_and_set("delete_rate", access_rates,
                                          10, int)
        add_input("Users",
                  "How many files are deleted per day?", i_delete_rate)
        delete_rate = 1.0 * delete_files_per_day / (24*60*60)


        # the value is in days
        lease_timers = [ (1, "one refresh per day"),
                         (7, "one refresh per week"),
                         ]
        lease_timer, i_lease = \
                     get_and_set("lease_timer", lease_timers,
                                 7, int)
        add_input("Users",
                  "How frequently do clients refresh files or accounts? "
                  "(if necessary)",
                  i_lease)
        seconds_per_lease = 24*60*60*lease_timer

        check_timer_choices = [ (1, "every week"),
                                (4, "every month"),
                                (8, "every two months"),
                                (16, "every four months"),
                                ]
        check_timer, i_check_timer = \
                     get_and_set("check_timer", check_timer_choices, 4, int)
        add_input("Users",
                  "How frequently should we check on each file?",
                  i_check_timer)
        file_check_interval = check_timer * 7 * 24 * 3600


        if filled:
            add_output("Users", T.div["Total users: %s" % number(num_users)])
            add_output("Users",
                       T.div["Files per user: %s" % number(files_per_user)])
            file_size = 1.0 * space_per_user / files_per_user
            add_output("Users",
                       T.div["Average file size: ", number(file_size)])
            total_files = num_users * files_per_user / sharing_ratio
            user_file_check_interval = file_check_interval / files_per_user

            add_output("Grid",
                       T.div["Total number of files in grid: ",
                             number(total_files)])
            total_space = num_users * space_per_user / sharing_ratio
            add_output("Grid",
                       T.div["Total volume of plaintext in grid: ",
                             number(total_space, "B")])

            total_shares = n * total_files
            add_output("Grid",
                       T.div["Total shares in grid: ", number(total_shares)])
            expansion = float(n) / float(k)

            total_usage = expansion * total_space
            add_output("Grid",
                       T.div["Share data in grid: ", number(total_usage, "B")])

            if n > num_servers:
                # silly configuration, causes Tahoe2 to wrap and put multiple
                # shares on some servers.
                add_output("Servers",
                           T.div["non-ideal: more shares than servers"
                                 " (n=%d, servers=%d)" % (n, num_servers)])
                # every file has at least one share on every server
                buckets_per_server = total_files
                shares_per_server = total_files * ((1.0 * n) / num_servers)
            else:
                # if nobody is full, then no lease requests will be turned
                # down for lack of space, and no two shares for the same file
                # will share a server. Therefore the chance that any given
                # file has a share on any given server is n/num_servers.
                buckets_per_server = total_files * ((1.0 * n) / num_servers)
                # since each such represented file only puts one share on a
                # server, the total number of shares per server is the same.
                shares_per_server = buckets_per_server
            add_output("Servers",
                       T.div["Buckets per server: ",
                             number(buckets_per_server)])
            add_output("Servers",
                       T.div["Shares per server: ",
                             number(shares_per_server)])

            # how much space is used on the storage servers for the shares?
            #  the share data itself
            share_data_per_server = total_usage / num_servers
            add_output("Servers",
                       T.div["Share data per server: ",
                             number(share_data_per_server, "B")])
            # this is determined empirically. H=hashsize=32, for a one-segment
            # file and 3-of-10 encoding
            share_validation_per_server = 266 * shares_per_server
            # this could be 423*buckets_per_server, if we moved the URI
            # extension into a separate file, but that would actually consume
            # *more* space (minimum filesize is 4KiB), unless we moved all
            # shares for a given bucket into a single file.
            share_uri_extension_per_server = 423 * shares_per_server

            # ownership mode adds per-bucket data
            H = 32 # depends upon the desired security of delete/refresh caps
            # bucket_lease_size is the amount of data needed to keep track of
            # the delete/refresh caps for each bucket.
            bucket_lease_size = 0
            client_bucket_refresh_rate = 0
            owner_table_size = 0
            if ownership_mode in ("B", "C", "D", "E"):
                bucket_lease_size = sharing_ratio * 1.0 * H
            if ownership_mode in ("B", "C"):
                # refreshes per second per client
                client_bucket_refresh_rate = (1.0 * n * files_per_user /
                                              seconds_per_lease)
                add_output("Users",
                           T.div["Client share refresh rate (outbound): ",
                                 number(client_bucket_refresh_rate, "Hz")])
                server_bucket_refresh_rate = (client_bucket_refresh_rate *
                                              num_users / num_servers)
                add_output("Servers",
                           T.div["Server share refresh rate (inbound): ",
                                 number(server_bucket_refresh_rate, "Hz")])
            if ownership_mode in ("D", "E"):
                # each server must maintain a bidirectional mapping from
                # buckets to owners. One way to implement this would be to
                # put a list of four-byte owner numbers into each bucket, and
                # a list of four-byte share numbers into each owner (although
                # of course we'd really just throw it into a database and let
                # the experts take care of the details).
                owner_table_size = 2*(buckets_per_server * sharing_ratio * 4)

            if ownership_mode in ("E",):
                # in this mode, clients must refresh one timer per server
                client_account_refresh_rate = (1.0 * num_servers /
                                               seconds_per_lease)
                add_output("Users",
                           T.div["Client account refresh rate (outbound): ",
                                 number(client_account_refresh_rate, "Hz")])
                server_account_refresh_rate = (client_account_refresh_rate *
                                              num_users / num_servers)
                add_output("Servers",
                           T.div["Server account refresh rate (inbound): ",
                                 number(server_account_refresh_rate, "Hz")])

            # TODO: buckets vs shares here is a bit wonky, but in
            # non-wrapping grids it shouldn't matter
            share_lease_per_server = bucket_lease_size * buckets_per_server
            share_ownertable_per_server = owner_table_size

            share_space_per_server = (share_data_per_server +
                                      share_validation_per_server +
                                      share_uri_extension_per_server +
                                      share_lease_per_server +
                                      share_ownertable_per_server)
            add_output("Servers",
                       T.div["Share space per server: ",
                             number(share_space_per_server, "B"),
                             " (data ",
                             number(share_data_per_server, "B"),
                             ", validation ",
                             number(share_validation_per_server, "B"),
                             ", UEB ",
                             number(share_uri_extension_per_server, "B"),
                             ", lease ",
                             number(share_lease_per_server, "B"),
                             ", ownertable ",
                             number(share_ownertable_per_server, "B"),
                             ")",
                             ])


            # rates
            client_download_share_rate = download_rate * k
            client_download_byte_rate = download_rate * file_size
            add_output("Users",
                       T.div["download rate: shares = ",
                             number(client_download_share_rate, "Hz"),
                             " , bytes = ",
                             number(client_download_byte_rate, "Bps"),
                             ])
            total_file_check_rate = 1.0 * total_files / file_check_interval
            client_check_share_rate = total_file_check_rate / num_users
            add_output("Users",
                       T.div["file check rate: shares = ",
                             number(client_check_share_rate, "Hz"),
                             " (interval = %s)" %
                             number(1 / client_check_share_rate, "s"),
                             ])

            client_upload_share_rate = upload_rate * n
            # TODO: doesn't include overhead
            client_upload_byte_rate = upload_rate * file_size * expansion
            add_output("Users",
                       T.div["upload rate: shares = ",
                             number(client_upload_share_rate, "Hz"),
                             " , bytes = ",
                             number(client_upload_byte_rate, "Bps"),
                             ])
            client_delete_share_rate = delete_rate * n

            server_inbound_share_rate = (client_upload_share_rate *
                                         num_users / num_servers)
            server_inbound_byte_rate = (client_upload_byte_rate *
                                        num_users / num_servers)
            add_output("Servers",
                       T.div["upload rate (inbound): shares = ",
                             number(server_inbound_share_rate, "Hz"),
                             " , bytes = ",
                              number(server_inbound_byte_rate, "Bps"),
                             ])
            add_output("Servers",
                       T.div["share check rate (inbound): ",
                             number(total_file_check_rate * n / num_servers,
                                    "Hz"),
                             ])

            server_share_modify_rate = ((client_upload_share_rate +
                                         client_delete_share_rate) *
                                         num_users / num_servers)
            add_output("Servers",
                       T.div["share modify rate: shares = ",
                             number(server_share_modify_rate, "Hz"),
                             ])

            server_outbound_share_rate = (client_download_share_rate *
                                          num_users / num_servers)
            server_outbound_byte_rate = (client_download_byte_rate *
                                         num_users / num_servers)
            add_output("Servers",
                       T.div["download rate (outbound): shares = ",
                             number(server_outbound_share_rate, "Hz"),
                             " , bytes = ",
                              number(server_outbound_byte_rate, "Bps"),
                             ])


            total_share_space = num_servers * share_space_per_server
            add_output("Grid",
                       T.div["Share space consumed: ",
                             number(total_share_space, "B")])
            add_output("Grid",
                       T.div[" %% validation: %.2f%%" %
                             (100.0 * share_validation_per_server /
                              share_space_per_server)])
            add_output("Grid",
                       T.div[" %% uri-extension: %.2f%%" %
                             (100.0 * share_uri_extension_per_server /
                              share_space_per_server)])
            add_output("Grid",
                       T.div[" %% lease data: %.2f%%" %
                             (100.0 * share_lease_per_server /
                              share_space_per_server)])
            add_output("Grid",
                       T.div[" %% owner data: %.2f%%" %
                             (100.0 * share_ownertable_per_server /
                              share_space_per_server)])
            add_output("Grid",
                       T.div[" %% share data: %.2f%%" %
                             (100.0 * share_data_per_server /
                              share_space_per_server)])
            add_output("Grid",
                       T.div["file check rate: ",
                             number(total_file_check_rate,
                                    "Hz")])

            total_drives = mathutil.div_ceil(int(total_share_space),
                                             int(drive_size))
            add_output("Drives",
                       T.div["Total drives: ", number(total_drives), " drives"])
            drives_per_server = mathutil.div_ceil(total_drives, num_servers)
            add_output("Servers",
                       T.div["Drives per server: ", drives_per_server])

            any_drive_failure_rate = total_drives * drive_failure_rate
            any_drive_MTBF = 1 // any_drive_failure_rate  # in seconds
            any_drive_MTBF_days = any_drive_MTBF / 86400
            add_output("Drives",
                       T.div["MTBF (any drive): ",
                             number(any_drive_MTBF_days), " days"])

            # availability
            file_dBA = self.file_availability(k, n, server_dBA)
            user_files_dBA = self.many_files_availability(file_dBA,
                                                          files_per_user)
            all_files_dBA = self.many_files_availability(file_dBA, total_files)
            add_output("Users",
                       T.div["availability of: ",
                             "arbitrary file = %d dBA, " % file_dBA,
                             "all files of user1 = %d dBA, " % user_files_dBA,
                             "all files in grid = %d dBA" % all_files_dBA,
                             ],
                       )

            time_until_files_lost = (n-k+1) / any_drive_failure_rate
            add_output("Grid",
                       T.div["avg time until files are lost: ",
                             number(time_until_files_lost, "s"), ", ",
                             number(time_until_files_lost/86400, " days"),
                             ])

            share_data_loss_rate = any_drive_failure_rate * drive_size
            add_output("Grid",
                       T.div["share data loss rate: ",
                             number(share_data_loss_rate,"Bps")])

            # the worst-case survival numbers occur when we do a file check
            # and the file is just above the threshold for repair (so we
            # decide to not repair it). The question is then: what is the
            # chance that the file will decay so badly before the next check
            # that we can't recover it? The resulting probability is per
            # check interval.
            # Note that the chances of us getting into this situation are low.
            P_disk_failure_during_interval = (drive_failure_rate *
                                              file_check_interval)
            disk_failure_dBF = 10*math.log10(P_disk_failure_during_interval)
            disk_failure_dBA = -disk_failure_dBF
            file_survives_dBA = self.file_availability(k, repair_threshold,
                                                       disk_failure_dBA)
            user_files_survives_dBA = self.many_files_availability( \
                file_survives_dBA, files_per_user)
            all_files_survives_dBA = self.many_files_availability( \
                file_survives_dBA, total_files)
            add_output("Users",
                       T.div["survival of: ",
                             "arbitrary file = %d dBA, " % file_survives_dBA,
                             "all files of user1 = %d dBA, " %
                             user_files_survives_dBA,
                             "all files in grid = %d dBA" %
                             all_files_survives_dBA,
                             " (per worst-case check interval)",
                             ])



        all_sections = []
        all_sections.append(build_section("Users"))
        all_sections.append(build_section("Servers"))
        all_sections.append(build_section("Drives"))
        if "Grid" in sections:
            all_sections.append(build_section("Grid"))

        f = T.form(action=".", method="post", enctype="multipart/form-data")

        if filled:
            action = "Recompute"
        else:
            action = "Compute"

        f = f[T.input(type="hidden", name="filled", value="true"),
              T.input(type="submit", value=action),
              all_sections,
              ]

        return f

    def file_availability(self, k, n, server_dBA):
        """
        The full formula for the availability of a specific file is::

         1 - sum([choose(N,i) * p**i * (1-p)**(N-i)] for i in range(k)])

        Where choose(N,i) = N! / ( i! * (N-i)! ) . Note that each term of
        this summation is the probability that there are exactly 'i' servers
        available, and what we're doing is adding up the cases where i is too
        low.

        This is a nuisance to calculate at all accurately, especially once N
        gets large, and when p is close to unity. So we make an engineering
        approximation: if (1-p) is very small, then each [i] term is much
        larger than the [i-1] term, and the sum is dominated by the i=k-1
        term. This only works for (1-p) < 10%, and when the choose() function
        doesn't rise fast enough to compensate. For high-expansion encodings
        (3-of-10, 25-of-100), the choose() function is rising at the same
        time as the (1-p)**(N-i) term, so that's not an issue. For
        low-expansion encodings (7-of-10, 75-of-100) the two values are
        moving in opposite directions, so more care must be taken.

        Note that the p**i term has only a minor effect as long as (1-p)*N is
        small, and even then the effect is attenuated by the 1-p term.
        """

        assert server_dBA > 9  # >=90% availability to use the approximation
        factor = binomial(n, k-1)
        factor_dBA = 10 * math.log10(factor)
        exponent = n - k + 1
        file_dBA = server_dBA * exponent - factor_dBA
        return file_dBA

    def many_files_availability(self, file_dBA, num_files):
        """The probability that 'num_files' independent bernoulli trials will
        succeed (i.e. we can recover all files in the grid at any given
        moment) is p**num_files . Since p is close to unity, we express in p
        in dBA instead, so we can get useful precision on q (=1-p), and then
        the formula becomes::

         P_some_files_unavailable = 1 - (1 - q)**num_files

        That (1-q)**n expands with the usual binomial sequence, 1 - nq +
        Xq**2 ... + Xq**n . We use the same approximation as before, since we
        know q is close to zero, and we get to ignore all the terms past -nq.
        """

        many_files_dBA = file_dBA - 10 * math.log10(num_files)
        return many_files_dBA
