#!python

# range of hash output lengths
range_L_hash = [128]

lg_M = 53                   # lg(required number of signatures before losing security)

limit_bytes = 480000        # limit on signature length
limit_cost = 500            # limit on Mcycles_Sig + weight_ver*Mcycles_ver
weight_ver = 1              # how important verification cost is relative to signature cost
                            # (note: setting this too high will just exclude useful candidates)

L_block = 512               # bitlength of hash input blocks
L_pad   = 64                # bitlength of hash padding overhead (for M-D hashes)
L_label = 80                # bitlength of hash position label
L_prf   = 256               # bitlength of hash output when used as a PRF
cycles_per_byte = 15.8      # cost of hash

Mcycles_per_block = cycles_per_byte * L_block / (8 * 1000000.0)


from math import floor, ceil, log, log1p, pow, e, sqrt
from sys import stderr
from gc import collect

def lg(x):
    return log(x, 2)
def ln(x):
    return log(x, e)
def ceil_log(x, B):
    return int(ceil(log(x, B)))
def ceil_div(x, y):
    return int(ceil(float(x) / float(y)))
def floor_div(x, y):
    return int(floor(float(x) / float(y)))

# number of compression function evaluations to hash k hash-outputs
# we assume that there is a label in each block
def compressions(k):
    return ceil_div(k + L_pad, L_block - L_label)

# sum of power series sum([pow(p, i) for i in range(n)])
def sum_powers(p, n):
    if p == 1: return n
    return (pow(p, n) - 1)/(p - 1)


def make_candidate(B, K, K1, K2, q, T, T_min, L_hash, lg_N, sig_bytes, c_sign, c_ver, c_ver_pm):
    Mcycles_sign   = c_sign   * Mcycles_per_block
    Mcycles_ver    = c_ver    * Mcycles_per_block
    Mcycles_ver_pm = c_ver_pm * Mcycles_per_block
    cost = Mcycles_sign + weight_ver*Mcycles_ver

    if sig_bytes >= limit_bytes or cost > limit_cost:
        return []

    return [{
        'B': B, 'K': K, 'K1': K1, 'K2': K2, 'q': q, 'T': T,
        'T_min': T_min,
        'L_hash': L_hash,
        'lg_N': lg_N,
        'sig_bytes': sig_bytes,
        'c_sign': c_sign,
        'Mcycles_sign': Mcycles_sign,
        'c_ver': c_ver,
        'c_ver_pm': c_ver_pm,
        'Mcycles_ver': Mcycles_ver,
        'Mcycles_ver_pm': Mcycles_ver_pm,
        'cost': cost,
    }]


# K1 = size of root Merkle tree
# K  = size of middle Merkle trees
# K2 = size of leaf Merkle trees
# q  = number of revealed private keys per signed message

# Winternitz with B < 4 is never optimal. For example, going from B=4 to B=2 halves the
# chain depth, but that is cancelled out by doubling (roughly) the number of digits.
range_B = xrange(4, 33)

M = pow(2, lg_M)

def calculate(K, K1, K2, q_max, L_hash, trees):
    candidates = []
    lg_K  = lg(K)
    lg_K1 = lg(K1)
    lg_K2 = lg(K2)

    # We want the optimal combination of q and T. That takes too much time and memory
    # to search for directly, so we start by calculating the lowest possible value of T
    # for any q. Then for potential values of T, we calculate the smallest q such that we
    # will have at least L_hash bits of security against forgery using revealed private keys
    # (i.e. this method of forgery is no easier than finding a hash preimage), provided
    # that fewer than 2^lg_S_min messages are signed.

    # min height of certification tree (excluding root and bottom layer)
    T_min = ceil_div(lg_M - lg_K1, lg_K)

    last_q = None
    for T in xrange(T_min, T_min+21):
        # lg(total number of leaf private keys)
        lg_S = lg_K1 + lg_K*T
        lg_N = lg_S + lg_K2

        # Suppose that m signatures have been made. The number of times X that a given bucket has
        # been chosen follows a binomial distribution B(m, p) where p = 1/S and S is the number of
        # buckets. I.e. Pr(X = x) = C(m, x) * p^x * (1-p)^(m-x).
        #
        # If an attacker picks a random seed and message that falls into a bucket that has been
        # chosen x times, then at most q*x private values in that bucket have been revealed, so
        # (ignoring the possibility of guessing private keys, which is negligable) the attacker's
        # success probability for a forgery using the revealed values is at most min(1, q*x / K2)^q.
        #
        # Let j = floor(K2/q). Conditioning on x, we have
        #
        # Pr(forgery) = sum_{x = 0..j}(Pr(X = x) * (q*x / K2)^q) + Pr(x > j)
        #             = sum_{x = 1..j}(Pr(X = x) * (q*x / K2)^q) + Pr(x > j)
        #
        # We lose nothing by approximating (q*x / K2)^q as 1 for x > 4, i.e. ignoring the resistance
        # of the HORS scheme to forgery when a bucket has been chosen 5 or more times.
        #
        # Pr(forgery) < sum_{x = 1..4}(Pr(X = x) * (q*x / K2)^q) + Pr(x > 4)
        #
        # where Pr(x > 4) = 1 - sum_{x = 0..4}(Pr(X = x))
        #
        # We use log arithmetic here because values very close to 1 cannot be represented accurately
        # in floating point, but their logarithms can (provided we use appropriate functions such as
        # log1p).

        lg_p = -lg_S
        lg_1_p = log1p(-pow(2, lg_p))/ln(2)        # lg(1-p), computed accurately
        j = 5
        lg_px = [lg_1_p * M]*j

        # We approximate lg(M-x) as lg(M)
        lg_px_step = lg_M + lg_p - lg_1_p
        for x in xrange(1, j):
            lg_px[x] = lg_px[x-1] - lg(x) + lg_px_step

        def find_min_q():
            for q in xrange(1, q_max+1):
                lg_q = lg(q)
                lg_pforge = [lg_px[x] + (lg_q*x - lg_K2)*q for x in xrange(1, j)]
                if max(lg_pforge) < -L_hash + lg(j) and lg_px[j-1] + 1.0 < -L_hash:
                    #print "K = %d, K1 = %d, K2 = %d, L_hash = %d, lg_K2 = %.3f, q = %d, lg_pforge_1 = %.3f, lg_pforge_2 = %.3f, lg_pforge_3 = %.3f" \
                    #      % (K, K1, K2, L_hash, lg_K2, q, lg_pforge_1, lg_pforge_2, lg_pforge_3)
                    return q
            return None

        q = find_min_q()
        if q is None or q == last_q:
            # if q hasn't decreased, this will be strictly worse than the previous candidate
            continue
        last_q = q

        # number of compressions to compute the Merkle hashes
        (h_M,  c_M,  _) = trees[K]
        (h_M1, c_M1, _) = trees[K1]
        (h_M2, c_M2, (dau, tri)) = trees[K2]

        # B = generalized Winternitz base
        for B in range_B:
            # n is the number of digits needed to sign the message representative and checksum.
            # The representation is base-B, except that we allow the most significant digit
            # to be up to 2B-1.
            n_L = ceil_div(L_hash-1, lg(B))
            firstL_max = floor_div(pow(2, L_hash)-1, pow(B, n_L-1))
            C_max = firstL_max + (n_L-1)*(B-1)
            n_C = ceil_log(ceil_div(C_max, 2), B)
            n = n_L + n_C
            firstC_max = floor_div(C_max, pow(B, n_C-1))

            # Total depth of Winternitz hash chains. The chains for the most significant
            # digit of the message representative and of the checksum may be a different
            # length to those for the other digits.
            c_D = (n-2)*(B-1) + firstL_max + firstC_max

            # number of compressions to hash a Winternitz public key
            c_W = compressions(n*L_hash + L_label)

            # bitlength of a single Winternitz signature and authentication path
            L_MW  = (n + h_M ) * L_hash
            L_MW1 = (n + h_M1) * L_hash

            # bitlength of the HORS signature and authentication paths
            # For all but one of the q authentication paths, one of the sibling elements in
            # another path is made redundant where they intersect. This cancels out the hash
            # that would otherwise be needed at the bottom of the path, making the total
            # length of the signature q*h_M2 + 1 hashes, rather than q*(h_M2 + 1).
            L_leaf = (q*h_M2 + 1) * L_hash

            # length of the overall GMSS+HORS signature and seeds
            sig_bytes = ceil_div(L_MW1 + T*L_MW + L_leaf + L_prf + ceil(lg_N), 8)

            c_MW  = K *(c_D + c_W) + c_M  + ceil_div(K *n*L_hash, L_prf)
            c_MW1 = K1*(c_D + c_W) + c_M1 + ceil_div(K1*n*L_hash, L_prf)

            # For simplicity, c_sign and c_ver don't take into account compressions saved
            # as a result of intersecting authentication paths in the HORS signature, so
            # are slight overestimates.

            c_sign = c_MW1 + T*c_MW + q*(c_M2 + 1) + ceil_div(K2*L_hash, L_prf)

            # *expected* number of compressions to verify a signature
            c_ver = c_D/2.0 + c_W + c_M1 + T*(c_D/2.0 + c_W + c_M) + q*(c_M2 + 1)
            c_ver_pm = (1 + T)*c_D/2.0

            candidates += make_candidate(B, K, K1, K2, q, T, T_min, L_hash, lg_N, sig_bytes, c_sign, c_ver, c_ver_pm)

    return candidates

def search():
    for L_hash in range_L_hash:
        print >>stderr, "collecting...   \r",
        collect()

        print >>stderr, "precomputing... \r",

        """
        # d/dq (lg(q+1) + L_hash/q) = 1/(ln(2)*(q+1)) - L_hash/q^2
        # Therefore lg(q+1) + L_hash/q is at a minimum when 1/(ln(2)*(q+1)) = L_hash/q^2.
        # Let alpha = L_hash*ln(2), then from the quadratic formula, the integer q that
        # minimizes lg(q+1) + L_hash/q is the floor or ceiling of (alpha + sqrt(alpha^2 - 4*alpha))/2.
        # (We don't want the other solution near 0.)

        alpha = floor(L_hash*ln(2))  # float
        q = floor((alpha + sqrt(alpha*(alpha-4)))/2)
        if lg(q+2) + L_hash/(q+1) < lg(q+1) + L_hash/q:
            q += 1
        lg_S_margin = lg(q+1) + L_hash/q
        q_max = int(q)

        q = floor(L_hash*ln(2))  # float
        if lg(q+1) + L_hash/(q+1) < lg(q) + L_hash/q:
            q += 1
        lg_S_margin = lg(q) + L_hash/q
        q_max = int(q)
        """
        q_max = 4000

        # find optimal Merkle tree shapes for this L_hash and each K
        trees = {}
        K_max = 50
        c2 = compressions(2*L_hash + L_label)
        c3 = compressions(3*L_hash + L_label)
        for dau in xrange(0, 10):
            a = pow(2, dau)
            for tri in xrange(0, ceil_log(30-dau, 3)):
                x = int(a*pow(3, tri))
                h = dau + 2*tri
                c_x = int(sum_powers(2, dau)*c2 + a*sum_powers(3, tri)*c3)
                for y in xrange(1, x+1):
                    if tri > 0:
                        # If the bottom level has arity 3, then for every 2 nodes by which the tree is
                        # imperfect, we can save c3 compressions by pruning 3 leaves back to their parent.
                        # If the tree is imperfect by an odd number of nodes, we can prune one extra leaf,
                        # possibly saving a compression if c2 < c3.
                        c_y = c_x - floor_div(x-y, 2)*c3 - ((x-y) % 2)*(c3-c2)
                    else:
                        # If the bottom level has arity 2, then for each node by which the tree is
                        # imperfect, we can save c2 compressions by pruning 2 leaves back to their parent.
                        c_y = c_x - (x-y)*c2

                    if y not in trees or (h, c_y, (dau, tri)) < trees[y]:
                        trees[y] = (h, c_y, (dau, tri))

        #for x in xrange(1, K_max+1):
        #    print x, trees[x]

        candidates = []
        progress = 0
        fuzz = 0
        complete = (K_max-1)*(2200-200)/100
        for K in xrange(2, K_max+1):
            for K2 in xrange(200, 2200, 100):
                for K1 in xrange(max(2, K-fuzz), min(K_max, K+fuzz)+1):
                    candidates += calculate(K, K1, K2, q_max, L_hash, trees)
                progress += 1
                print >>stderr, "searching: %3d %% \r" % (100.0 * progress / complete,),

        print >>stderr, "filtering...    \r",
        step = 2.0
        bins = {}
        limit = floor_div(limit_cost, step)
        for bin in xrange(0, limit+2):
            bins[bin] = []

        for c in candidates:
            bin = floor_div(c['cost'], step)
            bins[bin] += [c]

        del candidates

        # For each in a range of signing times, find the best candidate.
        best = []
        for bin in xrange(0, limit):
            candidates = bins[bin] + bins[bin+1] + bins[bin+2]
            if len(candidates) > 0:
                best += [min(candidates, key=lambda c: c['sig_bytes'])]

        def format_candidate(candidate):
            return ("%(B)3d  %(K)3d  %(K1)3d  %(K2)5d %(q)4d %(T)4d  "
                    "%(L_hash)4d   %(lg_N)5.1f  %(sig_bytes)7d   "
                    "%(c_sign)7d (%(Mcycles_sign)7.2f) "
                    "%(c_ver)7d +/-%(c_ver_pm)5d (%(Mcycles_ver)5.2f +/-%(Mcycles_ver_pm)5.2f)   "
                   ) % candidate

        print >>stderr, "                \r",
        if len(best) > 0:
            print "  B    K   K1     K2    q    T  L_hash  lg_N  sig_bytes  c_sign (Mcycles)        c_ver     (    Mcycles   )"
            print "---- ---- ---- ------ ---- ---- ------ ------ --------- ------------------ --------------------------------"

            best.sort(key=lambda c: (c['sig_bytes'], c['cost']))
            last_sign = None
            last_ver = None
            for c in best:
                if last_sign is None or c['c_sign'] < last_sign or c['c_ver'] < last_ver:
                    print format_candidate(c)
                    last_sign = c['c_sign']
                    last_ver = c['c_ver']

            print
        else:
            print "No candidates found for L_hash = %d or higher." % (L_hash)
            return

        del bins
        del best

print "Maximum signature size: %d bytes" % (limit_bytes,)
print "Maximum (signing + %d*verification) cost: %.1f Mcycles" % (weight_ver, limit_cost)
print "Hash parameters: %d-bit blocks with %d-bit padding and %d-bit labels, %.2f cycles per byte" \
      % (L_block, L_pad, L_label, cycles_per_byte)
print "PRF output size: %d bits" % (L_prf,)
print "Security level given by L_hash is maintained for up to 2^%d signatures.\n" % (lg_M,)

search()
