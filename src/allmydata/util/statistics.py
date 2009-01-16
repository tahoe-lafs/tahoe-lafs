# Copyright (c) 2009 Shawn Willden
# mailto:shawn@willden.org

from __future__ import division
from mathutil import round_sigfigs
import math
import array

def pr_file_loss(p_list, k):
    """
    Probability of single-file loss for shares with reliabilities in
    p_list.

    Computes the probability that a single file will become
    unrecoverable, based on the individual share survival
    probabilities and and k (number of shares needed for recovery).

    Example: pr_file_loss([.9] * 5 + [.99] * 5, 3) returns the
    probability that a file with k=3, N=10 and stored on five servers
    with reliability .9 and five servers with reliability .99 is lost.

    See survival_pmf docstring for important statistical assumptions.

    """
    assert 0 < k <= len(p_list)
    assert valid_probability_list(p_list)

    # Sum elements 0 through k-1 of the share set PMF to get the
    # probability that less than k shares survived.
    return sum(survival_pmf(p_list)[0:k])

def survival_pmf(p_list):
    """
    Return the collective PMF of share survival count for a set of
    shares with the individual survival probabilities in p_list.

    Example: survival_pmf([.99] * 10 + [.8] * 6) returns the
    probability mass function for the number of shares that will
    survive from an initial set of 16, 10 with p=0.99 and 6 with
    p=0.8.  The ith element of the resulting list is the probability
    that exactly i shares will survive.

    This calculation makes the following assumptions:

    1.  p_list[i] is the probability that any individual share will
    will survive during the time period in question (whatever that may
    be).

    2.  The share failures are "independent", in the statistical
    sense.  Note that if a group of shares are stored on the same
    machine or even in the same data center, they are NOT independent
    and this calculation is therefore wrong.
    """
    assert valid_probability_list(p_list)

    pmf = survival_pmf_via_conv(p_list)

    assert valid_pmf(pmf)
    return pmf

def survival_pmf_via_bd(p_list):
    """
    Compute share survival PMF using the binomial distribution PMF as
    much as possible.

    This is more efficient than the convolution method below, but
    doesn't work for large numbers of shares because the
    binomial_coeff calculation blows up.  Since the efficiency gains
    only matter in the case of large numbers of shares, it's pretty
    much useless except for testing the convolution methond.

    Note that this function does little to no error checking and is
    intended for internal use and testing only.
    """
    pmf_list = [ binomial_distribution_pmf(p_list.count(p), p) 
                 for p in set(p_list) ]
    return reduce(convolve, pmf_list)

def survival_pmf_via_conv(p_list):
    """
    Compute share survival PMF using iterated convolution of trivial
    PMFs.

    Note that this function does little to no error checking and is
    intended for internal use and testing only.
    """
    pmf_list = [ [1 - p, p] for p in p_list ];
    return reduce(convolve, pmf_list)

def print_pmf(pmf, n=4):
    """
    Print a PMF in a readable form, with values rounded to n
    significant digits. 
    """
    for k, p in enumerate(pmf):
        print "i=" + str(k) + ":", round_sigfigs(p, n)

def pr_backup_file_loss(p_list, backup_p, k):
    """
    Probability of single-file loss in a backup context

    Same as pr_file_loss, except it factors in the probability of
    survival of the original source, specified as backup_p.  Because
    that's a precondition to caring about the availability of the
    backup, it's an independent event.
    """
    assert valid_probability_list(p_list)
    assert 0 < backup_p <= 1
    assert 0 < k <= len(p_list)

    return pr_file_loss(p_list, k) * (1 - backup_p)


def find_k(p_list, target_loss_prob):
    """
    Find the highest k value that achieves the targeted loss
    probability, given the share reliabilities given in p_list.
    """
    assert valid_probability_list(p_list)
    assert 0 < target_loss_prob < 1

    pmf = survival_pmf(p_list)
    return find_k_from_pmf(pmf, target_loss_prob)

def find_k_from_pmf(pmf, target_loss_prob):
    """
    Find the highest k value that achieves the targeted loss 
    probability, given the share survival PMF given in pmf.
    """
    assert valid_pmf(pmf)
    assert 0 < target_loss_prob < 1

    loss_prob = 0.0
    for k, p_k in enumerate(pmf):
        loss_prob += p_k
        if loss_prob > target_loss_prob:
            return k

    k = len(pmf) - 1
    return k

def repair_count_pmf(survival_pmf, k):
    """
    Return Pr[D=d], where D represents the number of shares that have
    to be repaired at the end of an interval, starting with a full
    set and subject to losses described in survival_pmf.
    """
    n = len(survival_pmf) - 1

    # Probability of 0 to repair is the probability of all shares
    # surviving plus the probability of less than k surviving.
    pmf = [ survival_pmf[n] + sum(survival_pmf[0:k]) ]
    
    # Probability of more than 0, up to N-k to repair
    for i in range(1, n-k+1):
        pmf.append(survival_pmf[n-i])
                   
    # Probability of more than N-k to repair is 0, because that means
    # there are less than k available and the file is irreparable.
    for i in range(n-k+1, n+1):
        pmf.append(0.0)

    assert(valid_pmf(pmf))
    return pmf

def bandwidth_cost_function(file_size, shares, k, ul_dl_ratio):
    return file_size + float(file_size) / k * shares * ul_dl_ratio

def mean_repair_cost(cost_function, file_size, survival_pmf, k):
    """
    Return the expected cost for a repair run on a file with the given
    survival_pmf and requiring k shares.
    """
    repair_pmf = repair_count_pmf(survival_pmf, k)
    exp_cnt = sum([d * repair_pmf[d] for d in range(1, len(repair_pmf))])
    return cost_function(file_size, exp_cnt, k)

def eternal_repair_cost(cost_function, file_size, survival_pmf, k, discount_rate=0):
    """
    Calculate the eternal repair cost for a file that is aggressively
    repaired.
    """
    c = mean_repair_cost(cost_function, file_size, survival_pmf, k)
    f = 1 - sum(survival_pmf[0:k])
    r = discount_rate

    return (c * (1-r)) / (1 - (1-r) * f)

def valid_pmf(pmf):
    """
    Validate that pmf looks like a proper discrete probability mass
    function in list form.

    Returns true if the elements of pmf sum to 1.
    """
    return round(sum(pmf),5) == 1.0

def valid_probability_list(p_list):
    """
    Validate that p_list is a list of probibilities
    """
    for p in p_list:
        if p < 0 or p > 1:
            return False

    return True

def convolve(list_a, list_b):
    """
    Returns the discrete convolution of two lists.

    Given two random variables X and Y, the convolution of their
    probability mass functions Pr(X) and Pr(Y) is equal to the
    Pr(X+Y).
    """
    n = len(list_a)
    m = len(list_b)
    
    result = []
    for i in range(n + m - 1):
        sum = 0.0

        lower = max(0, i - n + 1)
        upper = min(m - 1, i)
        
        for j in range(lower, upper+1):
            sum += list_a[i-j] * list_b[j]

        result.append(sum)

    return result

def binomial_distribution_pmf(n, p):
    """
    Returns Pr(K), where K ~ B(n,p), as a list of values.

    Returns the full probability mass function of a B(n, p) as a list
    of values, where the kth element is Pr(K=k), or, in the Tahoe
    context, the probability that exactly k copies of a file share
    survive, when placed on n independent servers with survival
    probability p.
    """
    assert p >= 0 and p <= 1, 'p=%s must be in the range [0,1]'%p
    assert n > 0

    result = []
    for k in range(n+1):
        result.append(math.pow(p    , k    ) * 
                      math.pow(1 - p, n - k) * 
                      binomial_coeff(n, k))

    assert valid_pmf(result)
    return result;

def binomial_coeff(n, k):
    """
    Returns the number of ways that k items can be chosen from a set
    of n.
    """
    assert n >= k

    if k > n:
        return 0

    if k > n/2:
        k = n - k

    accum = 1.0
    for i in range(1, k+1):
        accum = accum * (n - k + i) // i;

    return int(accum + 0.5)
