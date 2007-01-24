/*
 * fec.h -- forward error correction based on Vandermonde matrices
 * 980614
 * (C) 1997-98 Luigi Rizzo (luigi@iet.unipi.it)
 *
 * Portions derived from code by Phil Karn (karn@ka9q.ampr.org),
 * Robert Morelos-Zaragoza (robert@spectra.eng.hawaii.edu) and Hari
 * Thirumoorthy (harit@spectra.eng.hawaii.edu), Aug 1995
 *
 * Modifications by Dan Rubenstein (see Modifications.txt for 
 * their description.
 * Modifications (C) 1998 Dan Rubenstein (drubenst@cs.umass.edu)
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:

 * 1. Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above
 *    copyright notice, this list of conditions and the following
 *    disclaimer in the documentation and/or other materials
 *    provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY THE AUTHORS ``AS IS'' AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
 * THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
 * PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE AUTHORS
 * BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
 * PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA,
 * OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR
 * TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT
 * OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY
 * OF SUCH DAMAGE.
 */

/*
 * If you get a error returned (negative value) from a fec_* function, 
 * look in here for the error message.
 */

extern char fec_error[];

typedef unsigned char gf;

typedef struct {
  unsigned long magic;
  unsigned char k, n;                     /* parameters of the code */
  gf *enc_matrix;
} fec_t;

void fec_free (fec_t *p);
fec_t *fec_new (unsigned char k, unsigned char n);

/**
 * @param inpkts the "primary shares" i.e. the chunks of the input data
 * @param fecs buffers into which the secondary shares will be written
 * @param share_ids the numbers of the desired shares -- including both primary shares (the id < k) which fec_encode_all() ignores and check shares (the id >= k) which fec_encode_all() will produce and store into the buffers of the fecs parameter
 * @param num_share_ids the length of the share_ids array
 */
void fec_encode_all(const fec_t* code, const gf*restrict const*restrict const src, gf*restrict const*restrict const fecs, const unsigned char*restrict const share_ids, unsigned char num_share_ids, size_t sz);

/**
 * @param inpkts an array of packets (size k)
 * @param outpkts an array of buffers into which the output packets will be written
 * @param index an array of the shareids of the packets in inpkts
 * @param sz size of a packet in bytes
 */
void fec_decode_all(const fec_t* code, const gf*restrict const*restrict const inpkts, gf*restrict const*restrict const outpkts, const unsigned char*restrict const index, size_t sz);

/* end of file */
