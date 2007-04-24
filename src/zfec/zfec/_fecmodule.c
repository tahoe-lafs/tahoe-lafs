/**
 * zfec -- fast forward error correction library with Python interface
 */

#include <Python.h>
#include <structmember.h>

#if (PY_VERSION_HEX < 0x02050000)
typedef int Py_ssize_t;
#endif

#include "fec.h"

#include "stdarg.h"

static PyObject *py_fec_error;
static PyObject *py_raise_fec_error (const char *format, ...);

static char fec__doc__[] = "\
FEC - Forward Error Correction \n\
";

static PyObject *
py_raise_fec_error(const char *format, ...) {
    char exceptionMsg[1024];
    va_list ap;

    va_start (ap, format);
    vsnprintf (exceptionMsg, 1024, format, ap);
    va_end (ap);
    exceptionMsg[1023]='\0';
    PyErr_SetString (py_fec_error, exceptionMsg);
    return NULL;
}

static char Encoder__doc__[] = "\
Hold static encoder state (an in-memory table for matrix multiplication), and k and m parameters, and provide {encode()} method.\n\n\
@param k: the number of packets required for reconstruction \n\
@param m: the number of packets generated \n\
";

typedef struct {
    PyObject_HEAD

    /* expose these */
    short kk;
    short mm;

    /* internal */
    fec_t* fec_matrix;
} Encoder;

static PyObject *
Encoder_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    Encoder *self;

    self = (Encoder*)type->tp_alloc(type, 0);
    if (self != NULL) {
        self->kk = 0;
        self->mm = 0;
        self->fec_matrix = NULL;
    }

    return (PyObject *)self;
}

static int
Encoder_init(Encoder *self, PyObject *args, PyObject *kwdict) {
    static char *kwlist[] = {
        "k",
        "m",
        NULL
    };
    int ink, inm;
    if (!PyArg_ParseTupleAndKeywords(args, kwdict, "ii", kwlist, &ink, &inm))
        return -1;

    if (ink < 1) {
        py_raise_fec_error("Precondition violation: first argument is required to be greater than or equal to 1, but it was %d", self->kk);
	return -1;
    }
    if (inm < 1) {
        py_raise_fec_error("Precondition violation: second argument is required to be greater than or equal to 1, but it was %d", self->mm);
	return -1;
    }
    if (inm > 256) {
        py_raise_fec_error("Precondition violation: second argument is required to be less than or equal to 256, but it was %d", self->mm);
	return -1;
    }
    if (ink > inm) {
        py_raise_fec_error("Precondition violation: first argument is required to be less than or equal to the second argument, but they were %d and %d respectively", ink, inm);
	return -1;
    }
    self->kk = (short)ink;
    self->mm = (short)inm;
    self->fec_matrix = fec_new(self->kk, self->mm);

    return 0;
}

static char Encoder_encode__doc__[] = "\
Encode data into m packets.\n\
\n\
@param inblocks: a sequence of k buffers of data to encode -- these are the k primary blocks, i.e. the input data split into k pieces (for best performance, make it a tuple instead of a list);  All blocks are required to be the same length.\n\
@param desired_blocks_nums optional sequence of blocknums indicating which blocks to produce and return;  If None, all m blocks will be returned (in order).  (For best performance, make it a tuple instead of a list.)\n\
@returns: a list of buffers containing the requested blocks; Note that if any of the input blocks were 'primary blocks', i.e. their blocknum was < k, then the result sequence will contain a Python reference to the same Python object as was passed in.  As long as the Python object in question is immutable (i.e. a string) then you don't have to think about this detail, but if it is mutable (i.e. an array), then you have to be aware that if you subsequently mutate the contents of that object then that will also change the contents of the sequence that was returned from this call to encode().\n\
";

static PyObject *
Encoder_encode(Encoder *self, PyObject *args) {
    PyObject* inblocks;
    PyObject* desired_blocks_nums = NULL; /* The blocknums of the blocks that should be returned. */
    PyObject* result = NULL;

    if (!PyArg_ParseTuple(args, "O|O", &inblocks, &desired_blocks_nums))
        return NULL;

    gf* check_blocks_produced[self->mm - self->kk]; /* This is an upper bound -- we will actually use only num_check_blocks_produced of these elements (see below). */
    PyObject* pystrs_produced[self->mm - self->kk]; /* This is an upper bound -- we will actually use only num_check_blocks_produced of these elements (see below). */
    unsigned num_check_blocks_produced = 0; /* The first num_check_blocks_produced elements of the check_blocks_produced array and of the pystrs_produced array will be used. */
    const gf* incblocks[self->kk];
    unsigned num_desired_blocks;
    PyObject* fast_desired_blocks_nums = NULL;
    PyObject** fast_desired_blocks_nums_items;
    unsigned c_desired_blocks_nums[self->mm];
    unsigned c_desired_checkblocks_ids[self->mm - self->kk];
    unsigned i;
    PyObject* fastinblocks = NULL;

    for (i=0; i<self->mm - self->kk; i++)
        pystrs_produced[i] = NULL;
    if (desired_blocks_nums) {
        fast_desired_blocks_nums = PySequence_Fast(desired_blocks_nums, "Second argument (optional) was not a sequence.");
        if (!fast_desired_blocks_nums)
            goto err;
        num_desired_blocks = PySequence_Fast_GET_SIZE(fast_desired_blocks_nums);
        fast_desired_blocks_nums_items = PySequence_Fast_ITEMS(fast_desired_blocks_nums);
        for (i=0; i<num_desired_blocks; i++) {
            if (!PyInt_Check(fast_desired_blocks_nums_items[i])) {
                py_raise_fec_error("Precondition violation: second argument is required to contain int.");
                goto err;
            }
            c_desired_blocks_nums[i] = PyInt_AsLong(fast_desired_blocks_nums_items[i]);
            if (c_desired_blocks_nums[i] >= self->kk)
                num_check_blocks_produced++;
        }
    } else {
        num_desired_blocks = self->mm;
        for (i=0; i<num_desired_blocks; i++)
            c_desired_blocks_nums[i] = i;
        num_check_blocks_produced = self->mm - self->kk;
    }

    fastinblocks = PySequence_Fast(inblocks, "First argument was not a sequence.");
    if (!fastinblocks)
        goto err;

    if (PySequence_Fast_GET_SIZE(fastinblocks) != self->kk) {
        py_raise_fec_error("Precondition violation: Wrong length -- first argument is required to contain exactly k blocks.  len(first): %d, k: %d", PySequence_Fast_GET_SIZE(fastinblocks), self->kk); 
        goto err;
    }

    /* Construct a C array of gf*'s of the input data. */
    PyObject** fastinblocksitems = PySequence_Fast_ITEMS(fastinblocks);
    if (!fastinblocksitems)
        goto err;
    Py_ssize_t sz, oldsz = 0;
    for (i=0; i<self->kk; i++) {
        if (!PyObject_CheckReadBuffer(fastinblocksitems[i])) {
            py_raise_fec_error("Precondition violation: %u'th item is required to offer the single-segment read character buffer protocol, but it does not.\n", i);
            goto err;
        }
        if (PyObject_AsReadBuffer(fastinblocksitems[i], (const void**)&(incblocks[i]), &sz))
            goto err;
        if (oldsz != 0 && oldsz != sz) {
            py_raise_fec_error("Precondition violation: Input blocks are required to be all the same length.  oldsz: %Zu, sz: %Zu\n", oldsz, sz);
            goto err;
        }
        oldsz = sz;
    }
    
    /* Allocate space for all of the check blocks. */
    unsigned char check_block_index = 0; /* index into the check_blocks_produced and (parallel) pystrs_produced arrays */
    for (i=0; i<num_desired_blocks; i++) {
        if (c_desired_blocks_nums[i] >= self->kk) {
            c_desired_checkblocks_ids[check_block_index] = c_desired_blocks_nums[i];
            pystrs_produced[check_block_index] = PyString_FromStringAndSize(NULL, sz);
            if (pystrs_produced[check_block_index] == NULL)
                goto err;
            check_blocks_produced[check_block_index] = (gf*)PyString_AsString(pystrs_produced[check_block_index]);
            if (check_blocks_produced[check_block_index] == NULL)
                goto err;
            check_block_index++;
        }
    }
    assert (check_block_index == num_check_blocks_produced);

    /* Encode any check blocks that are needed. */
    fec_encode(self->fec_matrix, incblocks, check_blocks_produced, c_desired_checkblocks_ids, num_check_blocks_produced, sz);

    /* Wrap all requested blocks up into a Python list of Python strings. */
    result = PyList_New(num_desired_blocks);
    if (result == NULL)
        goto err;
    check_block_index = 0;
    for (i=0; i<num_desired_blocks; i++) {
        if (c_desired_blocks_nums[i] < self->kk) {
            Py_INCREF(fastinblocksitems[c_desired_blocks_nums[i]]);
            if (PyList_SetItem(result, i, fastinblocksitems[c_desired_blocks_nums[i]]) == -1) {
                Py_DECREF(fastinblocksitems[c_desired_blocks_nums[i]]);
                goto err;
            }
        } else {
            if (PyList_SetItem(result, i, pystrs_produced[check_block_index]) == -1)
                goto err;
            pystrs_produced[check_block_index] = NULL;
            check_block_index++;
        }
    }

    goto cleanup;
  err:
    for (i=0; i<num_check_blocks_produced; i++)
        Py_XDECREF(pystrs_produced[i]);
    Py_XDECREF(result); result = NULL;
  cleanup:
    Py_XDECREF(fastinblocks); fastinblocks=NULL;
    Py_XDECREF(fast_desired_blocks_nums); fast_desired_blocks_nums=NULL;
    return result;
}

static void
Encoder_dealloc(Encoder * self) {
    fec_free(self->fec_matrix);
    self->ob_type->tp_free((PyObject*)self);
}

static PyMethodDef Encoder_methods[] = {
    {"encode", (PyCFunction)Encoder_encode, METH_VARARGS, Encoder_encode__doc__},
    {NULL},
};

static PyMemberDef Encoder_members[] = {
    {"k", T_SHORT, offsetof(Encoder, kk), READONLY, "k"},
    {"m", T_SHORT, offsetof(Encoder, mm), READONLY, "m"},
    {NULL} /* Sentinel */
};

static PyTypeObject Encoder_type = {
    PyObject_HEAD_INIT(NULL)
    0,                         /*ob_size*/
    "_fec.Encoder", /*tp_name*/
    sizeof(Encoder),             /*tp_basicsize*/
    0,                         /*tp_itemsize*/
    (destructor)Encoder_dealloc, /*tp_dealloc*/
    0,                         /*tp_print*/
    0,                         /*tp_getattr*/
    0,                         /*tp_setattr*/
    0,                         /*tp_compare*/
    0,                         /*tp_repr*/
    0,                         /*tp_as_number*/
    0,                         /*tp_as_sequence*/
    0,                         /*tp_as_mapping*/
    0,                         /*tp_hash */
    0,                         /*tp_call*/
    0,                         /*tp_str*/
    0,                         /*tp_getattro*/
    0,                         /*tp_setattro*/
    0,                         /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /*tp_flags*/
    Encoder__doc__,           /* tp_doc */
    0,		               /* tp_traverse */
    0,		               /* tp_clear */
    0,		               /* tp_richcompare */
    0,		               /* tp_weaklistoffset */
    0,		               /* tp_iter */
    0,		               /* tp_iternext */
    Encoder_methods,             /* tp_methods */
    Encoder_members,             /* tp_members */
    0,                         /* tp_getset */
    0,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    (initproc)Encoder_init,      /* tp_init */
    0,                         /* tp_alloc */
    Encoder_new,                 /* tp_new */
};

static char Decoder__doc__[] = "\
Hold static decoder state (an in-memory table for matrix multiplication), and k and m parameters, and provide {decode()} method.\n\n\
@param k: the number of packets required for reconstruction \n\
@param m: the number of packets generated \n\
";

typedef struct {
    PyObject_HEAD

    /* expose these */
    short kk;
    short mm;

    /* internal */
    fec_t* fec_matrix;
} Decoder;

static PyObject *
Decoder_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    Decoder *self;

    self = (Decoder*)type->tp_alloc(type, 0);
    if (self != NULL) {
        self->kk = 0;
        self->mm = 0;
        self->fec_matrix = NULL;
    }

    return (PyObject *)self;
}

static int
Decoder_init(Encoder *self, PyObject *args, PyObject *kwdict) {
    static char *kwlist[] = {
        "k",
        "m",
        NULL
    };

    int ink, inm;
    if (!PyArg_ParseTupleAndKeywords(args, kwdict, "ii", kwlist, &ink, &inm))
        return -1;

    if (ink < 1) {
        py_raise_fec_error("Precondition violation: first argument is required to be greater than or equal to 1, but it was %d", self->kk);
	return -1;
    }
    if (inm < 1) {
        py_raise_fec_error("Precondition violation: second argument is required to be greater than or equal to 1, but it was %d", self->mm);
	return -1;
    }
    if (inm > 256) {
        py_raise_fec_error("Precondition violation: second argument is required to be less than or equal to 256, but it was %d", self->mm);
	return -1;
    }
    if (ink > inm) {
        py_raise_fec_error("Precondition violation: first argument is required to be less than or equal to the second argument, but they were %d and %d respectively", ink, inm);
	return -1;
    }
    self->kk = (short)ink;
    self->mm = (short)inm;
    self->fec_matrix = fec_new(self->kk, self->mm);

    return 0;
}

#define SWAP(a,b,t) {t tmp; tmp=a; a=b; b=tmp;}

static char Decoder_decode__doc__[] = "\
Decode a list blocks into a list of segments.\n\
@param blocks a sequence of buffers containing block data (for best performance, make it a tuple instead of a list)\n\
@param blocknums a sequence of integers of the blocknum for each block in blocks (for best performance, make it a tuple instead of a list)\n\
\n\
@return a list of strings containing the segment data (i.e. ''.join(retval) yields a string containing the decoded data)\n\
";

static PyObject *
Decoder_decode(Decoder *self, PyObject *args) {
    PyObject*restrict blocks;
    PyObject*restrict blocknums;
    PyObject* result = NULL;

    if (!PyArg_ParseTuple(args, "OO", &blocks, &blocknums))
        return NULL;

    const gf*restrict cblocks[self->kk];
    unsigned cblocknums[self->kk];
    gf*restrict recoveredcstrs[self->kk]; /* self->kk is actually an upper bound -- we probably won't need all of this space. */
    PyObject*restrict recoveredpystrs[self->kk]; /* self->kk is actually an upper bound -- we probably won't need all of this space. */
    unsigned i;
    for (i=0; i<self->kk; i++)
        recoveredpystrs[i] = NULL;
    PyObject*restrict fastblocknums = NULL;
    PyObject*restrict fastblocks = PySequence_Fast(blocks, "First argument was not a sequence.");
    if (!fastblocks)
        goto err;
    fastblocknums = PySequence_Fast(blocknums, "Second argument was not a sequence.");
    if (!fastblocknums)
        goto err;

    if (PySequence_Fast_GET_SIZE(fastblocks) != self->kk) {
        py_raise_fec_error("Precondition violation: Wrong length -- first argument is required to contain exactly k blocks.  len(first): %d, k: %d", PySequence_Fast_GET_SIZE(fastblocks), self->kk); 
        goto err;
    }
    if (PySequence_Fast_GET_SIZE(fastblocknums) != self->kk) {
        py_raise_fec_error("Precondition violation: Wrong length -- blocknums is required to contain exactly k blocks.  len(blocknums): %d, k: %d", PySequence_Fast_GET_SIZE(fastblocknums), self->kk); 
        goto err;
    }

    /* Construct a C array of gf*'s of the data and another of C ints of the blocknums. */
    unsigned needtorecover=0;
    PyObject** fastblocknumsitems = PySequence_Fast_ITEMS(fastblocknums);
    if (!fastblocknumsitems)
        goto err;
    PyObject** fastblocksitems = PySequence_Fast_ITEMS(fastblocks);
    if (!fastblocksitems)
        goto err;
    Py_ssize_t sz, oldsz = 0;
    for (i=0; i<self->kk; i++) {
        if (!PyInt_Check(fastblocknumsitems[i])) {
            py_raise_fec_error("Precondition violation: second argument is required to contain int.");
            goto err;
        }
        long tmpl = PyInt_AsLong(fastblocknumsitems[i]);
        if (tmpl < 0 || tmpl > 255) {
            py_raise_fec_error("Precondition violation: block nums can't be less than zero or greater than 255.  %ld\n", tmpl);
            goto err;
        }
        cblocknums[i] = (unsigned)tmpl;
        if (cblocknums[i] >= self->kk)
            needtorecover+=1;

        if (!PyObject_CheckReadBuffer(fastblocksitems[i])) {
            py_raise_fec_error("Precondition violation: %u'th item is required to offer the single-segment read character buffer protocol, but it does not.\n", i);
            goto err;
        }
        if (PyObject_AsReadBuffer(fastblocksitems[i], (const void**)&(cblocks[i]), &sz))
            goto err;
        if (oldsz != 0 && oldsz != sz) {
            py_raise_fec_error("Precondition violation: Input blocks are required to be all the same length.  oldsz: %Zu, sz: %Zu\n", oldsz, sz);
            goto err;
        }
        oldsz = sz;
    }

    /* move src packets into position */
    for (i=0; i<self->kk;) {
        if (cblocknums[i] >= self->kk || cblocknums[i] == i)
            i++;
        else {
            /* put pkt in the right position. */
            unsigned c = cblocknums[i];

            SWAP (cblocknums[i], cblocknums[c], int);
            SWAP (cblocks[i], cblocks[c], const gf*);
            SWAP (fastblocksitems[i], fastblocksitems[c], PyObject*);
        }
    }

    /* Allocate space for all of the recovered blocks. */
    for (i=0; i<needtorecover; i++) {
        recoveredpystrs[i] = PyString_FromStringAndSize(NULL, sz);
        if (recoveredpystrs[i] == NULL)
            goto err;
        recoveredcstrs[i] = (gf*)PyString_AsString(recoveredpystrs[i]);
        if (recoveredcstrs[i] == NULL)
            goto err;
    }

    /* Decode any recovered blocks that are needed. */
    fec_decode(self->fec_matrix, cblocks, recoveredcstrs, cblocknums, sz);

    /* Wrap up both original primary blocks and decoded blocks into a Python list of Python strings. */
    unsigned nextrecoveredix=0;
    result = PyList_New(self->kk);
    if (result == NULL)
        goto err;
    for (i=0; i<self->kk; i++) {
        if (cblocknums[i] == i) {
            /* Original primary block. */
            Py_INCREF(fastblocksitems[i]);
            if (PyList_SetItem(result, i, fastblocksitems[i]) == -1) {
                Py_DECREF(fastblocksitems[i]);
                goto err;
            }
        } else {
            /* Recovered block. */
            if (PyList_SetItem(result, i, recoveredpystrs[nextrecoveredix]) == -1)
                goto err;
            recoveredpystrs[nextrecoveredix] = NULL;
            nextrecoveredix++;
        }
    }

    goto cleanup;
  err:
    for (i=0; i<self->kk; i++)
        Py_XDECREF(recoveredpystrs[i]);
    Py_XDECREF(result); result = NULL;
  cleanup:
    Py_XDECREF(fastblocks); fastblocks=NULL;
    Py_XDECREF(fastblocknums); fastblocknums=NULL;
    return result;
}

static void
Decoder_dealloc(Decoder * self) {
    fec_free(self->fec_matrix);
    self->ob_type->tp_free((PyObject*)self);
}

static PyMethodDef Decoder_methods[] = {
    {"decode", (PyCFunction)Decoder_decode, METH_VARARGS, Decoder_decode__doc__},
    {NULL},
};

static PyMemberDef Decoder_members[] = {
    {"k", T_SHORT, offsetof(Encoder, kk), READONLY, "k"},
    {"m", T_SHORT, offsetof(Encoder, mm), READONLY, "m"},
    {NULL} /* Sentinel */
};

static PyTypeObject Decoder_type = {
    PyObject_HEAD_INIT(NULL)
    0,                         /*ob_size*/
    "_fec.Decoder", /*tp_name*/
    sizeof(Decoder),             /*tp_basicsize*/
    0,                         /*tp_itemsize*/
    (destructor)Decoder_dealloc, /*tp_dealloc*/
    0,                         /*tp_print*/
    0,                         /*tp_getattr*/
    0,                         /*tp_setattr*/
    0,                         /*tp_compare*/
    0,                         /*tp_repr*/
    0,                         /*tp_as_number*/
    0,                         /*tp_as_sequence*/
    0,                         /*tp_as_mapping*/
    0,                         /*tp_hash */
    0,                         /*tp_call*/
    0,                         /*tp_str*/
    0,                         /*tp_getattro*/
    0,                         /*tp_setattro*/
    0,                         /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /*tp_flags*/
    Decoder__doc__,           /* tp_doc */
    0,		               /* tp_traverse */
    0,		               /* tp_clear */
    0,		               /* tp_richcompare */
    0,		               /* tp_weaklistoffset */
    0,		               /* tp_iter */
    0,		               /* tp_iternext */
    Decoder_methods,             /* tp_methods */
    Decoder_members,             /* tp_members */
    0,                         /* tp_getset */
    0,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    (initproc)Decoder_init,      /* tp_init */
    0,                         /* tp_alloc */
    Decoder_new,                 /* tp_new */
};

static PyMethodDef fec_methods[] = { 
    {NULL} 
};

#ifndef PyMODINIT_FUNC	/* declarations for DLL import/export */
#define PyMODINIT_FUNC void
#endif
PyMODINIT_FUNC
init_fec(void) {
    PyObject *module;
    PyObject *module_dict;

    if (PyType_Ready(&Encoder_type) < 0)
        return;
    if (PyType_Ready(&Decoder_type) < 0)
        return;

    module = Py_InitModule3("_fec", fec_methods, fec__doc__);
    if (module == NULL)
      return;

    Py_INCREF(&Encoder_type);
    Py_INCREF(&Decoder_type);

    PyModule_AddObject(module, "Encoder", (PyObject *)&Encoder_type);
    PyModule_AddObject(module, "Decoder", (PyObject *)&Decoder_type);

    module_dict = PyModule_GetDict(module);
    py_fec_error = PyErr_NewException("_fec.Error", NULL, NULL);
    PyDict_SetItemString(module_dict, "Error", py_fec_error);
}

/**
 * zfec -- fast forward error correction library with Python interface
 *
 * Copyright (C) 2007 Allmydata, Inc.
 * Author: Zooko Wilcox-O'Hearn
 * mailto:zooko@zooko.com
 *
 * This file is part of zfec.
 * 
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.  This program also
 * comes with the added permission that, in the case that you are obligated to
 * release a derived work under this licence (as per section 2.b of the GPL),
 * you may delay the fulfillment of this obligation for up to 12 months.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
 */

/**
 * based on fecmodule.c by the Mnet Project, especially Myers Carpenter and
 * Hauke Johannknecht
 */

