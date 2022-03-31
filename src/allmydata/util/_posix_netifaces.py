
from cffi import FFI
from socket import inet_ntop, AF_INET, AF_INET6

ffi = FFI()
ffi.cdef("""
struct in_addr {
  union {
    struct {
      unsigned char s_b1;
      unsigned char s_b2;
      unsigned char s_b3;
      unsigned char s_b4;
    } S_un_b;
    struct {
      unsigned short s_w1;
      unsigned short s_w2;
    } S_un_w;
    unsigned long S_addr;
  } S_un;
};

struct in6_addr {
        uint8_t  s6_addr[16];  /* IPv6 address */
};

struct sockaddr {
        unsigned short  sa_family;
        char            sa_data[14];
};

struct sockaddr_in {
   unsigned short   sin_family;
   unsigned short   sin_port;
   struct in_addr   sin_addr;
};

struct sockaddr_in6 {
       unsigned char    sin6_len;      /* length of this structure */
       unsigned char    sin6_family;   /* AF_INET6                 */
       uint16_t         sin6_port;     /* Transport layer port #   */
       uint32_t         sin6_flowinfo; /* IPv6 flow information    */
       struct in6_addr  sin6_addr;     /* IPv6 address             */
};

struct ifaddrs {
    struct ifaddrs  *ifa_next;    /* Next item in list */
    char            *ifa_name;    /* Name of interface */
    unsigned int     ifa_flags;   /* Flags from SIOCGIFFLAGS */
    struct sockaddr *ifa_addr;    /* Address of interface */
    struct sockaddr *ifa_netmask; /* Netmask of interface */
};

int getifaddrs(struct ifaddrs **ifap);
void freeifaddrs(struct ifaddrs *ifa);
""")
_C = ffi.dlopen(None)

def interfaces():
    result = {}

    # The memory allocated is garbage collected along with ifaddrs_p
    ifaddrs_p = ffi.new("struct ifaddrs**")

    errno = _C.getifaddrs(ifaddrs_p)
    if errno == 0:
        try:
            # Success, read the values.
            ifaddr = ifaddrs_p[0]
            while ifaddr != ffi.NULL:
                addr = _sockaddr_to_address(ifaddr.ifa_addr)
                if addr is not None:
                    result.setdefault(ffi.string(ifaddr.ifa_name), []).append(addr)
                ifaddr = ifaddr.ifa_next
        finally:
            # The ifaddrs structs themselves are dynamically allocated by
            # getifaddrs and need to be freed.
            _C.freeifaddrs(ifaddrs_p[0])
    else:
        raise OSError(errno)

    return result


def _sockaddr_to_address(sockaddr):
    if sockaddr.sa_family == AF_INET:
        sockaddr_x = ffi.cast("struct sockaddr_in*", sockaddr)
        offset = 4
        size = 4
    elif sockaddr.sa_family == AF_INET6:
        sockaddr_x = ffi.cast("struct sockaddr_in6*", sockaddr)
        offset = 8
        size = 16
    else:
        return None

    buf = ffi.buffer(sockaddr_x, offset + size)[offset:offset + size]
    return inet_ntop(sockaddr.sa_family, buf)
