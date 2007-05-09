/**
 * zfec -- fast forward error correction library with Python interface
 */

typedef unsigned char gf;

typedef struct {
  unsigned long magic;
  unsigned k, n;                     /* parameters of the code */
  gf* enc_matrix;
} fec_t;

/**
 * param k the number of blocks required to reconstruct
 * param m the total number of blocks created
 */
fec_t* fec_new(unsigned k, unsigned m);
void fec_free(fec_t* p);

/**
 * @param inpkts the "primary blocks" i.e. the chunks of the input data
 * @param fecs buffers into which the secondary blocks will be written
 * @param block_nums the numbers of the desired blocks -- including both primary blocks (the id < k) which fec_encode() ignores and check blocks (the id >= k) which fec_encode() will produce and store into the buffers of the fecs parameter
 * @param num_block_nums the length of the block_nums array
 */
void fec_encode(const fec_t* code, const gf*restrict const*restrict const src, gf*restrict const*restrict const fecs, const unsigned*restrict const block_nums, size_t num_block_nums, size_t sz);

/**
 * @param inpkts an array of packets (size k)
 * @param outpkts an array of buffers into which the reconstructed output packets will be written (only packets which are not present in the inpkts input will be reconstructed and written to outpkts)
 * @param index an array of the blocknums of the packets in inpkts
 * @param sz size of a packet in bytes
 */
void fec_decode(const fec_t* code, const gf*restrict const*restrict const inpkts, gf*restrict const*restrict const outpkts, const unsigned*restrict const index, size_t sz);


/**
 * zfec -- fast forward error correction library with Python interface
 * 
 * Copyright (C) 2007 Allmydata, Inc.
 * Author: Zooko Wilcox-O'Hearn
 * 
 * This file is part of zfec.
 * 
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the Free
 * Software Foundation; either version 2 of the License, or (at your option)
 * any later version, with the added permission that, if you become obligated
 * to release a derived work under this licence (as per section 2.b), you may
 * delay the fulfillment of this obligation for up to 12 months.  See the file
 * COPYING for details.
 *
 * If you would like to inquire about a commercial relationship with Allmydata,
 * Inc., please contact partnerships@allmydata.com and visit
 * http://allmydata.com/.
 */

/*
 * Much of this work is derived from the "fec" software by Luigi Rizzo, et 
 * al., the copyright notice and licence terms of which are included below 
 * for reference.
 * 
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

