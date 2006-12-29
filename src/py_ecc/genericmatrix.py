
# Copyright Emin Martinian 2002.  See below for license terms.
# Version Control Info: $Id: genericmatrix.py,v 1.7 2003/10/28 21:18:41 emin Exp $

"""
This package implements the GenericMatrix class to provide matrix
operations for any type that supports the multiply, add, subtract,
and divide operators.  For example, this package can be used to
do matrix calculations over finite fields using the ffield package
available at http://martinian.com.

The following docstrings provide detailed information on various topics:

  GenericMatrix.__doc__   Describes the methods of the GenericMatrix
                          class and how to use them.

  license_doc             Describes the license and lack of warranty
                          for this code.

  testing_doc             Describes some tests to make sure the code works.

"""

import operator

class GenericMatrix:

    """
    The GenericMatrix class implements a matrix with works with
    any generic type supporting addition, subtraction, multiplication,
    and division.  Matrix multiplication, addition, and subtraction
    are implemented as are methods for finding inverses,
    LU (actually LUP) decompositions, and determinants.  A complete
    list of user callable methods is:

    __init__
    __repr__
    __mul__
    __add__
    __sub__
    __setitem__
    __getitem__
    Size
    SetRow
    GetRow
    GetColumn
    Copy
    MakeSimilarMatrix
    SwapRows
    MulRow
    AddRow
    AddCol
    MulAddRow
    LeftMulColumnVec
    LowerGaussianElim
    Inverse
    Determinant
    LUP

    A quick and dirty example of how to use the GenericMatrix class
    for matricies of floats is provided below.
    
>>> import genericmatrix
>>> v = genericmatrix.GenericMatrix((3,3))
>>> v.SetRow(0,[0.0, -1.0, 1.0])
>>> v.SetRow(1,[1.0, 1.0, 1.0])
>>> v.SetRow(2,[1.0, 1.0, -1.0])
>>> v
<matrix
  0.0 -1.0  1.0
  1.0  1.0  1.0
  1.0  1.0 -1.0>
>>> vi = v.Inverse()
>>> vi
<matrix
  1.0  0.0  1.0
 -1.0  0.5 -0.5
 -0.0  0.5 -0.5>
>>> (vi * v) - v.MakeSimilarMatrix(v.Size(),'i')
<matrix
 0.0 0.0 0.0
 0.0 0.0 0.0
 0.0 0.0 0.0>

# See what happens when we try to invert a non-invertible matrix

>>> v[0,1] = 0.0
>>> v
<matrix
  0.0  0.0  1.0
  1.0  1.0  1.0
  1.0  1.0 -1.0>
>>> abs(v.Determinant())
0.0
>>> v.Inverse()
Traceback (most recent call last):
     ...
ValueError: matrix not invertible

# LUP decomposition will still work even if Inverse() won't.

>>> (l,u,p) = v.LUP()
>>> l
<matrix
 1.0 0.0 0.0
 0.0 1.0 0.0
 1.0 0.0 1.0>
>>> u
<matrix
  1.0  1.0  1.0
  0.0  0.0  1.0
  0.0  0.0 -2.0>
>>> p
<matrix
 0.0 1.0 0.0
 1.0 0.0 0.0
 0.0 0.0 1.0>
>>> p * v - l * u
<matrix
 0.0 0.0 0.0
 0.0 0.0 0.0
 0.0 0.0 0.0>

# Operate on some column vectors using v.
# The LeftMulColumnVec methods lets us do this without having
# to construct a new GenericMatrix to represent each column vector.
>>> v.LeftMulColumnVec([1.0,2.0,3.0])
[3.0, 6.0, 0.0]
>>> v.LeftMulColumnVec([1.0,-2.0,1.0])
[1.0, 0.0, -2.0]

# Most of the stuff above could be done with something like matlab.
# But, with this package you can do matrix ops for finite fields.
>>> XOR = lambda x,y: x^y
>>> AND = lambda x,y: x&y
>>> DIV = lambda x,y: x  
>>> m = GenericMatrix(size=(3,4),zeroElement=0,identityElement=1,add=XOR,mul=AND,sub=XOR,div=DIV)
>>> m.SetRow(0,[0,1,0,0])
>>> m.SetRow(1,[0,1,0,1])
>>> m.SetRow(2,[0,0,1,0])
>>> # You can't invert m since it isn't square, but you can still 
>>> # get the LUP decomposition or solve a system of equations.
>>> (l,u,p) = v.LUP()
>>> p*v-l*u
<matrix
 0.0 0.0 0.0
 0.0 0.0 0.0
 0.0 0.0 0.0>
>>> b = [1,0,1]
>>> x = m.Solve(b)
>>> b == m.LeftMulColumnVec(x)
1

    """

   
    def __init__(self, size=(2,2), zeroElement=0.0, identityElement=1.0,
                 add=operator.__add__, sub=operator.__sub__,
                 mul=operator.__mul__, div = operator.__div__,
                 eq = operator.__eq__, str=lambda x:`x`,
                 equalsZero = None,fillMode='z'):
        """
        Function:     __init__(size,zeroElement,identityElement,
                               add,sub,mul,div,eq,str,equalsZero fillMode)

        Description:  This is the constructor for the GenericMatrix
                      class.  All arguments are optional and default
                      to producing a 2-by-2 zero matrix for floats.
                      A detailed description of arguments follows:

             size: A tuple of the form (numRows, numColumns)
                   zeroElement: An object representing the additive
                   identity (i.e. 'zero') for the data
                   type of interest.
                   
             identityElement: An object representing the multiplicative
                              identity (i.e. 'one') for the data
                              type of interest.

             add,sub,mul,div: Functions implementing basic arithmetic
                              operations for the type of interest.

             eq: A function such that eq(x,y) == 1 if and only if x == y.

             str: A function used to produce a string representation of
                  the type of interest.

             equalsZero: A function used to decide if an element is
                         essentially zero.  For floats, you could use
                         lambda x: abs(x) < 1e-6.

             fillMode: This can either be 'e' in which case the contents
                       of the matrix are left empty, 'z', in which case
                       the matrix is filled with zeros, 'i' in which
                       case an identity matrix is created, or a two
                       argument function which is called with the row
                       and column of each index and produces the value
                       for that entry.  Default is 'z'.
        """
        if (None == equalsZero):
            equalsZero = lambda x: self.eq(self.zeroElement,x)

        self.equalsZero = equalsZero
        self.add = add
        self.sub = sub
        self.mul = mul
        self.div = div
        self.eq = eq
        self.str = str
        self.zeroElement = zeroElement
        self.identityElement = identityElement
        self.rows, self.cols = size
        self.data = []


        def q(x,y,z):
            if (x):
                return y
            else:
                return z

        if (fillMode == 'e'):
            return
        elif (fillMode == 'z'):
            fillMode = lambda x,y: self.zeroElement
        elif (fillMode == 'i'):
            fillMode = lambda x,y: q(self.eq(x,y),self.identityElement,
                                     self.zeroElement)

        for i in range(self.rows):
            self.data.append(map(fillMode,[i]*self.cols,range(self.cols)))

    def MakeSimilarMatrix(self,size,fillMode):
        """
        MakeSimilarMatrix(self,size,fillMode)

        Return a matrix of the given size filled according to fillMode
        with the same zeroElement, identityElement, add, sub, etc.
        as self.

        For example, self.MakeSimilarMatrix(self.Size(),'i') returns
        an identity matrix of the same shape as self.
        """
        return GenericMatrix(size=size,zeroElement=self.zeroElement,
                               identityElement=self.identityElement,
                               add=self.add,sub=self.sub,
                               mul=self.mul,div=self.div,eq=self.eq,
                               str=self.str,equalsZero=self.equalsZero,
                               fillMode=fillMode)
        

    def __repr__(self):
        m = 0
        # find the fattest element
        for r in self.data:
            for c in r:
                l = len(self.str(c))
                if l > m:
                    m = l
        f = '%%%ds' % (m+1)
        s = '<matrix'
        for r in self.data:
            s = s + '\n'
            for c in r:
                s = s + (f % self.str(c))
        s = s + '>'
        return s

    def __mul__(self,other):
        if (self.cols != other.rows):
            raise ValueError, "dimension mismatch"
        result = self.MakeSimilarMatrix((self.rows,other.cols),'z')
                               
        for i in range(self.rows):
            for j in range(other.cols):
                result.data[i][j] = reduce(self.add,
                                           map(self.mul,self.data[i],
                                               other.GetColumn(j)))
        return result

    def __add__(self,other):
        if (self.cols != other.rows):
            raise ValueError, "dimension mismatch"
        result = self.MakeSimilarMatrix(size=self.Size(),fillMode='z')
        for i in range(self.rows):
            for j in range(other.cols):
                result.data[i][j] = self.add(self.data[i][j],other.data[i][j])
        return result
    
    def __sub__(self,other):
        if (self.cols != other.cols or self.rows != other.rows):
            raise ValueError, "dimension mismatch"
        result = self.MakeSimilarMatrix(size=self.Size(),fillMode='z')
        for i in range(self.rows):
            for j in range(other.cols):
                result.data[i][j] = self.sub(self.data[i][j],
                                             other.data[i][j])
        return result

    def __setitem__ (self, (x,y), data):
        "__setitem__((x,y),data) sets item row x and column y to data."
        self.data[x][y] = data

    def __getitem__ (self, (x,y)):
        "__getitem__((x,y)) gets item at row x and column y."
        return self.data[x][y]

    def Size (self):
        "returns (rows, columns)"
        return (len(self.data), len(self.data[0]))

    def SetRow(self,r,result):
        "SetRow(r,result) sets row r to result."
        
        assert len(result) == self.cols, ('Wrong # columns in row: ' +
                                          'expected ' + `self.cols` + ', got '
                                          + `len(result)`)
        self.data[r] = list(result)

    def GetRow(self,r):
        "GetRow(r) returns a copy of row r."
        return list(self.data[r])

    def GetColumn(self,c):
        "GetColumn(c) returns a copy of column c."
        if (c >= self.cols):
            raise ValueError, 'matrix does not have that many columns'
        result = []
        for r in self.data:
            result.append(r[c])
        return result

    def Transpose(self):
        oldData = self.data
        self.data = []
        for r in range(self.cols):
            self.data.append([])
            for c in range(self.rows):
                self.data[r].append(oldData[c][r])
        rows = self.rows
        self.rows = self.cols
        self.cols = rows

    def Copy(self):
        result = self.MakeSimilarMatrix(size=self.Size(),fillMode='e')

        for r in self.data:
            result.data.append(list(r))
        return result

    def SubMatrix(self,rowStart,rowEnd,colStart=0,colEnd=None):
        """
        SubMatrix(self,rowStart,rowEnd,colStart,colEnd)
        Create and return a sub matrix containg rows
        rowStart through rowEnd (inclusive) and columns
        colStart through colEnd (inclusive).
        """
        if (not colEnd):
            colEnd = self.cols-1
        if (rowEnd >= self.rows):
            raise ValueError, 'rowEnd too big: rowEnd >= self.rows'
        result = self.MakeSimilarMatrix((rowEnd-rowStart+1,colEnd-colStart+1),
                                        'e')

        for i in range(rowStart,rowEnd+1):
            result.data.append(list(self.data[i][colStart:(colEnd+1)]))

        return result
 
    def UnSubMatrix(self,rowStart,rowEnd,colStart,colEnd):
        """
        UnSubMatrix(self,rowStart,rowEnd,colStart,colEnd)
        Create and return a sub matrix containg everything except
        rows rowStart through rowEnd (inclusive) 
        and columns colStart through colEnd (inclusive).
        """
        result = self.MakeSimilarMatrix((self.rows-(rowEnd-rowStart),
                                         self.cols-(colEnd-colStart)),'e')

        for i in range(0,rowStart) + range(rowEnd,self.rows):
            result.data.append(list(self.data[i][0:colStart] +
                                    self.data[i][colEnd:]))

        return result


    def SwapRows(self,i,j):
        temp = list(self.data[i])
        self.data[i] = list(self.data[j])
        self.data[j] = temp

    def MulRow(self,r,m,start=0):
        """
        Function: MulRow(r,m,start=0)
        Multiply row r by m starting at optional column start (default 0).
        """
        row = self.data[r]
        for i in range(start,self.cols):
            row[i] = self.mul(row[i],m)

    def AddRow(self,i,j):
        """
        Add row i to row j.
        """
        self.data[j] = map(self.add,self.data[i],self.data[j])

    def AddCol(self,i,j):
        """
        Add column i to column j.
        """
        for r in range(self.rows):
            self.data[r][j] = self.add(self.data[r][i],self.data[r][j])

    def MulAddRow(self,m,i,j):
        """
        Multiply row i by m and add to row j.
        """
        self.data[j] = map(self.add,
                           map(self.mul,[m]*self.cols,self.data[i]),
                           self.data[j])

    def LeftMulColumnVec(self,colVec):
        """
        Function:       LeftMulColumnVec(c)
        Purpose:        Compute the result of self * c.
        Description:    This function taks as input a list c,
                        computes the desired result and returns it
                        as a list.  This is sometimes more convenient
                        than constructed a new GenericMatrix to represent
                        c, computing the result and extracting c to a list.
        """
        if (self.cols != len(colVec)):
            raise ValueError, 'dimension mismatch'
        result = range(self.rows)
        for r in range(self.rows):
            result[r] = reduce(self.add,map(self.mul,self.data[r],colVec))
        return result

    def FindRowLeader(self,startRow,c):
        for r in range(startRow,self.rows):
            if (not self.eq(self.zeroElement,self.data[r][c])):
                return r
        return -1

    def FindColLeader(self,r,startCol):
        for c in range(startCol,self.cols):
            if (not self.equalsZero(self.data[r][c])):
                return c
        return -1    

    def PartialLowerGaussElim(self,rowIndex,colIndex,resultInv):
        """
        Function: PartialLowerGaussElim(rowIndex,colIndex,resultInv)
        
        This function does partial Gaussian elimination on the part of
        the matrix on and below the main diagonal starting from
        rowIndex.  In addition to modifying self, this function
        applies the required elmentary row operations to the input
        matrix resultInv.

        By partial, what we mean is that if this function encounters
        an element on the diagonal which is 0, it stops and returns
        the corresponding rowIndex.  The caller can then permute
        self or apply some other operation to eliminate the zero
        and recall PartialLowerGaussElim.
        
        This function is meant to be combined with UpperInverse
        to compute inverses and LU decompositions.
        """

        lastRow = self.rows-1
        while (rowIndex < lastRow):
            if (colIndex >= self.cols):
                return (rowIndex, colIndex)
            if (self.eq(self.zeroElement,self.data[rowIndex][colIndex])):
                # self[rowIndex,colIndex] = 0 so quit.
                return (rowIndex, colIndex)
            divisor = self.div(self.identityElement,
                               self.data[rowIndex][colIndex])
            for k in range(rowIndex+1,self.rows):
                nextTerm = self.data[k][colIndex]
                if (self.zeroElement != nextTerm):
                    multiple = self.mul(divisor,self.sub(self.zeroElement,
                                                         nextTerm))
                    self.MulAddRow(multiple,rowIndex,k)
                    resultInv.MulAddRow(multiple,rowIndex,k)
            rowIndex = rowIndex + 1
            colIndex = colIndex + 1
        return (rowIndex, colIndex)

    def LowerGaussianElim(self,resultInv=''):
        """
        Function:       LowerGaussianElim(r)
        Purpose:        Perform Gaussian elimination on self to eliminate
                        all terms below the diagonal.
        Description:    This method modifies self via Gaussian elimination
                        and applies the elementary row operations used in
                        this transformation to the input matrix, r
                        (if one is provided, otherwise a matrix with
                         identity elements on the main diagonal is
                         created to serve the role of r).

                        Thus if the input, r, is an identity matrix, after
                        the call it will represent the transformation
                        made to perform Gaussian elimination.

                        The matrix r is returned.
        """
        if (resultInv == ''):
            resultInv = self.MakeSimilarMatrix(self.Size(),'i')
            
        (rowIndex,colIndex) = (0,0)
        lastRow = min(self.rows - 1,self.cols)
        lastCol = self.cols - 1
        while( rowIndex < lastRow and colIndex < lastCol):
            leader = self.FindRowLeader(rowIndex,colIndex)
            if (leader < 0):
                colIndex = colIndex + 1
                continue
            if (leader != rowIndex):
                resultInv.AddRow(leader,rowIndex)
                self.AddRow(leader,rowIndex)
            (rowIndex,colIndex) = (
                self.PartialLowerGaussElim(rowIndex,colIndex,resultInv))
        return resultInv

    def UpperInverse(self,resultInv=''):
        """
        Function: UpperInverse(resultInv)
        
        Assumes that self is an upper triangular matrix like

          [a b c ... ]
          [0 d e ... ]
          [0 0 f ... ]
          [.     .   ]
          [.      .  ]
          [.       . ]

        and performs Gaussian elimination to transform self into
        the identity matrix.  The required elementary row operations
        are applied to the matrix resultInv passed as input.  For
        example, if the identity matrix is passed as input, then the
        value returned is the inverse of self before the function
        was called.

        If no matrix, resultInv, is provided as input then one is
        created with identity elements along the main diagonal.
        In either case, resultInv is returned as output.
        """
        if (resultInv == ''):
            resultInv = self.MakeSimilarMatrix(self.Size(),'i')
        lastCol = min(self.rows,self.cols)
        for colIndex in range(0,lastCol):
            if (self.zeroElement == self.data[colIndex][colIndex]):
                raise ValueError, 'matrix not invertible'
            divisor = self.div(self.identityElement,
                               self.data[colIndex][colIndex])
            if (self.identityElement != divisor):
                self.MulRow(colIndex,divisor,colIndex)
                resultInv.MulRow(colIndex,divisor)
            for rowToElim in range(0,colIndex):
                multiple = self.sub(self.zeroElement,
                                    self.data[rowToElim][colIndex])
                self.MulAddRow(multiple,colIndex,rowToElim)
                resultInv.MulAddRow(multiple,colIndex,rowToElim)
        return resultInv
    
    def Inverse(self):
        """
        Function:       Inverse
        Description:    Returns the inverse of self without modifying
                        self.  An exception is raised if the matrix
                        is not invertable.
        """

        workingCopy = self.Copy()
        result = self.MakeSimilarMatrix(self.Size(),'i')
        workingCopy.LowerGaussianElim(result)
        workingCopy.UpperInverse(result)
        return result

    def Determinant(self):
        """
        Function:       Determinant
        Description:    Returns the determinant of the matrix or raises
                        a ValueError if the matrix is not square.
        """
        if (self.rows != self.cols):
            raise ValueError, 'matrix not square'
        workingCopy = self.Copy()
        result = self.MakeSimilarMatrix(self.Size(),'i')
        workingCopy.LowerGaussianElim(result)
        det = self.identityElement
        for i in range(self.rows):
            det = det * workingCopy.data[i][i]
        return det

    def LUP(self):
        """
        Function:       (l,u,p) = self.LUP()
        Purpose:        Compute the LUP decomposition of self.
        Description:    This function returns three matrices
                        l, u, and p such that p * self = l * u
                        where l, u, and p have the following properties:

                        l is lower triangular with ones on the diagonal
                        u is upper triangular 
                        p is a permutation matrix.

                        The idea behind the algorithm is to first
                        do Gaussian elimination to obtain an upper
                        triangular matrix u and lower triangular matrix
                        r such that r * self = u, then by inverting r to
                        get l = r ^-1 we obtain self = r^-1 * u = l * u.
                        Note tha since r is lower triangular its
                        inverse must also be lower triangular.

                        Where does the p come in?  Well, with some
                        matrices our technique doesn't work due to
                        zeros appearing on the diagonal of r.  So we
                        apply some permutations to the orginal to
                        prevent this.
                        
        """
        upper = self.Copy()
        resultInv = self.MakeSimilarMatrix(self.Size(),'i')
        perm = self.MakeSimilarMatrix((self.rows,self.rows),'i')

        (rowIndex,colIndex) = (0,0)
        lastRow = self.rows - 1
        lastCol = self.cols - 1
        while( rowIndex < lastRow and colIndex < lastCol ):
            leader = upper.FindRowLeader(rowIndex,colIndex)
            if (leader < 0):
                colIndex = colIndex+1
                continue            
            if (leader != rowIndex):
                upper.SwapRows(leader,rowIndex)
                resultInv.SwapRows(leader,rowIndex)
                perm.SwapRows(leader,rowIndex)
            (rowIndex,colIndex) = (
                upper.PartialLowerGaussElim(rowIndex,colIndex,resultInv))

        lower = self.MakeSimilarMatrix((self.rows,self.rows),'i')
        resultInv.LowerGaussianElim(lower)
        resultInv.UpperInverse(lower)
        # possible optimization: due perm*lower explicitly without
        # relying on the * operator.
        return (perm*lower, upper, perm)
    
    def Solve(self,b):
        """
        Solve(self,b):

        b:   A list.
        
        Returns the values of x such that Ax = b.

        This is done using the LUP decomposition by 
        noting that Ax = b implies PAx = Pb implies LUx = Pb.
        First we solve for Ly = Pb and then we solve Ux = y.
        The following is an example of how to use Solve:

>>> # Floating point example
>>> import genericmatrix
>>> A = genericmatrix.GenericMatrix(size=(2,5),str=lambda x: '%.4f' % x)
>>> A.SetRow(0,[0.0, 0.0, 0.160, 0.550, 0.280])
>>> A.SetRow(1,[0.0, 0.0, 0.745, 0.610, 0.190])
>>> A
<matrix
 0.0000 0.0000 0.1600 0.5500 0.2800
 0.0000 0.0000 0.7450 0.6100 0.1900>
>>> b = [0.975, 0.350]
>>> x = A.Solve(b)
>>> z = A.LeftMulColumnVec(x)
>>> diff = reduce(lambda xx,yy: xx+yy,map(lambda aa,bb: abs(aa-bb),b,z))
>>> diff > 1e-6
0
>>> # Boolean example
>>> XOR = lambda x,y: x^y
>>> AND = lambda x,y: x&y
>>> DIV = lambda x,y: x  
>>> m=GenericMatrix(size=(3,6),zeroElement=0,identityElement=1,add=XOR,mul=AND,sub=XOR,div=DIV)
>>> m.SetRow(0,[1,0,0,1,0,1])
>>> m.SetRow(1,[0,1,1,0,1,0])
>>> m.SetRow(2,[0,1,0,1,1,0])
>>> b = [0, 1, 1]
>>> x = m.Solve(b)
>>> z = m.LeftMulColumnVec(x)
>>> z
[0, 1, 1]

        """
        assert self.cols >= self.rows
        
        (L,U,P) = self.LUP()
        Pb = P.LeftMulColumnVec(b)
        y = [0]*len(Pb)
        for row in range(L.rows):
            y[row] = Pb[row]
            for i in range(row+1,L.rows):
                Pb[i] = L.sub(Pb[i],L.mul(L[i,row],Pb[row]))
        x = [0]*self.cols
        curRow = self.rows-1

        for curRow in range(len(y)-1,-1,-1):
            col = U.FindColLeader(curRow,0)
            assert col > -1
            x[col] = U.div(y[curRow],U[curRow,col])
            y[curRow] = x[col]
            for i in range(0,curRow):
                y[i] = U.sub(y[i],U.mul(U[i,col],y[curRow]))
        return x


def DotProduct(mul,add,x,y):
    """
    Function:    DotProduct(mul,add,x,y)
    Description: Return the dot product of lists x and y using mul and
                 add as the multiplication and addition operations.
    """
    assert len(x) == len(y), 'sizes do not match'
    return reduce(add,map(mul,x,y))

class GenericMatrixTester:
    def DoTests(self,numTests,sizeList):
        """
        Function:       DoTests(numTests,sizeList)

        Description:    For each test, run numTests tests for square
                        matrices with the sizes in sizeList.
        """

        for size in sizeList:
            self.RandomInverseTest(size,numTests)
            self.RandomLUPTest(size,numTests)
            self.RandomSolveTest(size,numTests)
            self.RandomDetTest(size,numTests)


    def MakeRandom(self,s):
        import random 
        r = GenericMatrix(size=s,fillMode=lambda x,y: random.random(),
                          equalsZero = lambda x: abs(x) < 1e-6)
        return r

    def MatAbs(self,m):
        r = -1
        (N,M) = m.Size()
        for i in range(0,N):
            for j in range(0,M):
                if (abs(m[i,j]) > r):
                    r = abs(m[i,j])
        return r

    def RandomInverseTest(self,s,n):
        ident = GenericMatrix(size=(s,s),fillMode='i')
        for i in range(n):
            m = self.MakeRandom((s,s))
            assert self.MatAbs(ident - m * m.Inverse()) < 1e-6, (
                'offender = ' + `m`)

    def RandomLUPTest(self,s,n):
        ident = GenericMatrix(size=(s,s),fillMode='i')
        for i in range(n):
            m = self.MakeRandom((s,s))
            (l,u,p) = m.LUP()
            assert self.MatAbs(p*m - l*u) < 1e-6, 'offender = ' + `m`

    def RandomSolveTest(self,s,n):
        import random
        if (s <= 1):
            return
        extraEquations=3
        
        for i in range(n):
            m = self.MakeRandom((s,s+extraEquations))
            for j in range(extraEquations):
                colToKill = random.randrange(s+extraEquations)
                for r in range(m.rows):
                    m[r,colToKill] = 0.0
            b = map(lambda x: random.random(), range(s))
            x = m.Solve(b)
            z = m.LeftMulColumnVec(x)
            diff = reduce(lambda xx,yy:xx+yy, map(lambda aa,bb:abs(aa-bb),b,z))
            assert diff < 1e-6, ('offenders: m = ' + `m` + '\nx = ' + `x`
                                 + '\nb = ' + `b` + '\ndiff = ' + `diff`)

    def RandomDetTest(self,s,n):
        for i in range(n):
            m1 = self.MakeRandom((s,s))
            m2 = self.MakeRandom((s,s))
            prod = m1 * m2
            assert (abs(m1.Determinant() * m2.Determinant()
                        - prod.Determinant() )
                    < 1e-6), 'offenders = ' + `m1` + `m2`


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

testing_doc = """
The GenericMatrixTester class contains some simple
testing functions such as RandomInverseTest, RandomLUPTest,
RandomSolveTest, and RandomDetTest which generate random floating
point values and test the appropriate routines.  The simplest way to
run these tests is via

>>> import genericmatrix
>>> t = genericmatrix.GenericMatrixTester()
>>> t.DoTests(100,[1,2,3,4,5,10])

# runs 100 tests each for sizes 1-5, 10
# note this may take a few minutes

If any problems occur, assertion errors are raised.  Otherwise
nothing is returned.  Note that you can also use the doctest
package to test all the python examples in the documentation
by typing 'python genericmatrix.py' or 'python -v genericmatrix.py' at the
command line.
"""


# The following code is used to make the doctest package
# check examples in docstrings when you enter

__test__ = {
    'testing_doc' : testing_doc
}

def _test():
    import doctest, genericmatrix
    return doctest.testmod(genericmatrix)

if __name__ == "__main__":
    _test()
    print 'Tests Passed.'
