
# Copyright Emin Martinian 2002.  See below for license terms.
# Version Control Info: $Id: rs_code.py,v 1.5 2003/07/04 01:30:05 emin Exp $

"""
This package implements the RSCode class designed to do
Reed-Solomon encoding and (erasure) decoding.  The following
docstrings provide detailed information on various topics.

  RSCode.__doc__   Describes the RSCode class and how to use it.

  license_doc      Describes the license and lack of warranty.

"""

import ffield
import genericmatrix
import math


class RSCode:
    """
    The RSCode class implements a Reed-Solomon code
    (currently, only erasure decoding not error decoding is
    implemented).  The relevant methods are:

    __init__
    Encode
    DecodeImmediate
    Decode
    PrepareDecoder
    RandomTest

    A breif example of how to use the code follows:
    
>>> import rs_code

# Create a coder for an (n,k) = (16,8) code and test 
# decoding for a simple erasure pattern.

>>> C = rs_code.RSCode(16,8) 
>>> inVec = range(8)         
>>> codedVec = C.Encode(inVec)
>>> receivedVec = list(codedVec)

# now erase some entries in the encoded vector by setting them to None
>>> receivedVec[3] = None; receivedVec[9] = None; receivedVec[12] = None
>>> receivedVec
[0, 1, 2, None, 4, 5, 6, 7, 8, None, 10, 11, None, 13, 14, 15]
>>> decVec = C.DecodeImmediate(receivedVec)
>>> decVec
[0, 1, 2, 3, 4, 5, 6, 7]

# Now try the random testing method for more complete coverage.
# Note this will take a while.
>>> for k in range(1,8):
...     for p in range(1,12):
...         C = rs_code.RSCode(k+p,k)
...         C.RandomTest(25)
>>> for k in range(1,8):
...     for p in range(1,12):
...         C = rs_code.RSCode(k+p,k,systematic=0)
...         C.RandomTest(25)
"""

    def __init__(self,n,k,log2FieldSize=-1,systematic=1,shouldUseLUT=-1):
        """
        Function:   __init__(n,k,log2FieldSize,systematic,shouldUseLUT)
        Purpose:    Create a Reed-Solomon coder for an (n,k) code.
        Notes:      The last parameters, log2FieldSize, systematic
                    and shouldUseLUT are optional.

                    The log2FieldSize parameter 
                    represents the base 2 logarithm of the field size.
                    If it is omitted, the field GF(2^p) is used where
                    p is the smalles integer where 2^p >= n.

                    If systematic is true then a systematic encoder
                    is created (i.e. one where the first k symbols
                    of the encoded result always match the data).

                    If shouldUseLUT = 1 then a lookup table is used for
                    computing finite field multiplies and divides.
                    If shouldUseLUT = 0 then no lookup table is used.
                    If shouldUseLUT = -1 (the default), then the code
                    decides when a lookup table should be used.
        """
        if (log2FieldSize < 0):
            log2FieldSize = int(math.ceil(math.log(n)/math.log(2)))
        self.field = ffield.FField(log2FieldSize,useLUT=shouldUseLUT)
        self.n = n
        self.k = k
        self.fieldSize = 1 << log2FieldSize
        self.CreateEncoderMatrix()
        if (systematic):
            self.encoderMatrix.Transpose()
            self.encoderMatrix.LowerGaussianElim()
            self.encoderMatrix.UpperInverse()
            self.encoderMatrix.Transpose()

    def __repr__(self):
        rep = ('<RSCode (n,k) = (' + `self.n` +', ' + `self.k` + ')'
               + '  over GF(2^' + `self.field.n` + ')\n' +
               `self.encoderMatrix` + '\n' + '>')
        return rep
               
    def CreateEncoderMatrix(self):                   
        self.encoderMatrix = genericmatrix.GenericMatrix(
            (self.n,self.k),0,1,self.field.Add,self.field.Subtract,
            self.field.Multiply,self.field.Divide)
        self.encoderMatrix[0,0] = 1
        for i in range(0,self.n):
            term = 1
            for j in range(0, self.k):
                self.encoderMatrix[i,j] = term
                term = self.field.Multiply(term,i)

    
    def Encode(self,data):
        """
        Function:       Encode(data)
        Purpose:        Encode a list of length k into length n.
        """
        assert len(data)==self.k, 'Encode: input data must be size k list.'
        
        return self.encoderMatrix.LeftMulColumnVec(data)

    def PrepareDecoder(self,unErasedLocations):
        """
        Function:       PrepareDecoder(erasedTerms)
        Description:    The input unErasedLocations is a list of the first
                        self.k elements of the codeword which were 
                        NOT erased.  For example, if the 0th, 5th,
                        and 7th symbols of a (16,5) code were erased,
                        then PrepareDecoder([1,2,3,4,6]) would
                        properly prepare for decoding.
        """
        if (len(unErasedLocations) != self.k):
            raise ValueError, 'input must be exactly length k'
        
        limitedEncoder = genericmatrix.GenericMatrix(
            (self.k,self.k),0,1,self.field.Add,self.field.Subtract,
            self.field.Multiply,self.field.Divide)
        for i in range(0,self.k):
            limitedEncoder.SetRow(
                i,self.encoderMatrix.GetRow(unErasedLocations[i]))
        self.decoderMatrix = limitedEncoder.Inverse()

    def Decode(self,unErasedTerms):
        """
        Function:       Decode(unErasedTerms)
        Purpose:        Use the
        Description:
        """
        return self.decoderMatrix.LeftMulColumnVec(unErasedTerms)

    def DecodeImmediate(self,data):
        """
        Function:       DecodeImmediate(data)
        Description:    Takes as input a data vector of length self.n
                        where erased symbols are set to None and
                        returns the decoded result provided that
                        at least self.k symbols are not None.

                        For example, for an (n,k) = (6,4) code, a
                        decodable input vector would be
                        [2, 0, None, 1, 2, None].
        """

        if (len(data) != self.n):
            raise ValueError, 'input must be a length n list'

        unErasedLocations = []
        unErasedTerms = []
        for i in range(self.n):
            if (None != data[i]):
                unErasedLocations.append(i)
                unErasedTerms.append(data[i])
        self.PrepareDecoder(unErasedLocations[0:self.k])
        return self.Decode(unErasedTerms[0:self.k])
        
    def RandomTest(self,numTests):
        import random
        
        maxErasures = self.n-self.k
        for i in range(numTests):
            inVec = range(self.k)
            for j in range(self.k):
                inVec[j] = random.randint(0, (1<<self.field.n)-1)
            codedVec = self.Encode(inVec)
            numErasures = random.randint(0,maxErasures)
            for j in range(numErasures):
                j = random.randint(0,self.n-1)
                while(codedVec[j] == None):
                    j = random.randint(0,self.n-1)
                codedVec[j] = None
            decVec = self.DecodeImmediate(codedVec)
            assert decVec == inVec, ('inVec = ' + `inVec`
                                     + '\ncodedVec = ' + `codedVec`
                                     + '\ndecVec = ' + `decVec`)

license_doc = """
  This code was originally written by Emin Martinian (emin@allegro.mit.edu).
  You may copy, modify, redistribute in source or binary form as long
  as credit is given to the original author.  Specifically, please
  include some kind of comment or docstring saying that Emin Martinian
  was one of the original authors.  Also, if you publish anything based
  on this work, it would be nice to cite the original author and any
  other contributers.

  There is NO WARRANTY for this software just as there is no warranty
  for GNU software (although this is not GNU software).  Specifically
  we adopt the same policy towards warranties as the GNU project:

  BECAUSE THE PROGRAM IS LICENSED FREE OF CHARGE, THERE IS NO WARRANTY
FOR THE PROGRAM, TO THE EXTENT PERMITTED BY APPLICABLE LAW.  EXCEPT WHEN
OTHERWISE STATED IN WRITING THE COPYRIGHT HOLDERS AND/OR OTHER PARTIES
PROVIDE THE PROGRAM 'AS IS' WITHOUT WARRANTY OF ANY KIND, EITHER EXPRESSED
OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.  THE ENTIRE RISK AS
TO THE QUALITY AND PERFORMANCE OF THE PROGRAM IS WITH YOU.  SHOULD THE
PROGRAM PROVE DEFECTIVE, YOU ASSUME THE COST OF ALL NECESSARY SERVICING,
REPAIR OR CORRECTION.

  IN NO EVENT UNLESS REQUIRED BY APPLICABLE LAW OR AGREED TO IN WRITING
WILL ANY COPYRIGHT HOLDER, OR ANY OTHER PARTY WHO MAY MODIFY AND/OR
REDISTRIBUTE THE PROGRAM AS PERMITTED ABOVE, BE LIABLE TO YOU FOR DAMAGES,
INCLUDING ANY GENERAL, SPECIAL, INCIDENTAL OR CONSEQUENTIAL DAMAGES ARISING
OUT OF THE USE OR INABILITY TO USE THE PROGRAM (INCLUDING BUT NOT LIMITED
TO LOSS OF DATA OR DATA BEING RENDERED INACCURATE OR LOSSES SUSTAINED BY
YOU OR THIRD PARTIES OR A FAILURE OF THE PROGRAM TO OPERATE WITH ANY OTHER
PROGRAMS), EVEN IF SUCH HOLDER OR OTHER PARTY HAS BEEN ADVISED OF THE
POSSIBILITY OF SUCH DAMAGES.
"""


# The following code is used to make the doctest package
# check examples in docstrings.

def _test():
    import doctest, rs_code
    return doctest.testmod(rs_code)

if __name__ == "__main__":
    _test()
    print 'Tests passed'
