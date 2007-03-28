/**
 * pyfec -- fast forward error correction library with Python interface
 *
 * Copyright (C) 2007 Allmydata, Inc.
 * Author: Zooko Wilcox-O'Hearn
 * mailto:zooko@zooko.com
 *
 * This file is part of pyfec.
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

#include <Python.h>
#include <structmember.h>

#if (PY_VERSION_HEX < 0x02050000)
typedef int Py_ssize_t;
#endif

#include "fec.h"

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
    int kk;
    int mm;

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
    if (!PyArg_ParseTupleAndKeywords(args, kwdict, "ii", kwlist, &self->kk, &self->mm))
        return -1;

    if (self->kk < 1) {
        py_raise_fec_error("Precondition violation: first argument is required to be greater than or equal to 1, but it was %d", self->kk);
	return -1;
    }
    if (self->mm < 1) {
        py_raise_fec_error("Precondition violation: second argument is required to be greater than or equal to 1, but it was %d", self->mm);
	return -1;
    }
    if (self->mm > 256) {
        py_raise_fec_error("Precondition violation: second argument is required to be less than or equal to 256, but it was %d", self->mm);
	return -1;
    }
    if (self->kk > self->mm) {
        py_raise_fec_error("Precondition violation: first argument is required to be less than or equal to the second argument, but they were %d and %d respectively", self->kk, self->mm);
	return -1;
    }
    self->fec_matrix = fec_new(self->kk, self->mm);

    return 0;
}

static char Encoder_encode__doc__[] = "\
Encode data into m packets.\n\
\n\
@param inshares: a sequence of k buffers of data to encode -- these are the k primary shares, i.e. the input data split into k pieces (for best performance, make it a tuple instead of a list);  All shares are required to be the same length.\n\
@param desired_shares_ids optional sequence of shareids indicating which shares to produce and return;  If None, all m shares will be returned (in order).  (For best performance, make it a tuple instead of a list.)\n\
@returns: a list of buffers containing the requested shares; Note that if any of the input shares were 'primary shares', i.e. their shareid was < k, then the result sequence will contain a Python reference to the same Python object as was passed in.  As long as the Python object in question is immutable (i.e. a string) then you don't have to think about this detail, but if it is mutable (i.e. an array), then you have to be aware that if you subsequently mutate the contents of that object then that will also change the contents of the sequence that was returned from this call to encode().\n\
";

static PyObject *
Encoder_encode(Encoder *self, PyObject *args) {
    PyObject* inshares;
    PyObject* desired_shares_ids = NULL; /* The shareids of the shares that should be returned. */
    PyObject* result = NULL;

    if (!PyArg_ParseTuple(args, "O|O", &inshares, &desired_shares_ids))
        return NULL;

    gf* check_shares_produced[self->mm - self->kk]; /* This is an upper bound -- we will actually use only num_check_shares_produced of these elements (see below). */
    PyObject* pystrs_produced[self->mm - self->kk]; /* This is an upper bound -- we will actually use only num_check_shares_produced of these elements (see below). */
    unsigned num_check_shares_produced = 0; /* The first num_check_shares_produced elements of the check_shares_produced array and of the pystrs_produced array will be used. */
    const gf* incshares[self->kk];
    unsigned num_desired_shares;
    PyObject* fast_desired_shares_ids = NULL;
    PyObject** fast_desired_shares_ids_items;
    unsigned c_desired_shares_ids[self->mm];
    unsigned c_desired_checkshares_ids[self->mm - self->kk];
    unsigned i;
    PyObject* fastinshares = NULL;

    for (i=0; i<self->mm - self->kk; i++)
        pystrs_produced[i] = NULL;
    if (desired_shares_ids) {
        fast_desired_shares_ids = PySequence_Fast(desired_shares_ids, "Second argument (optional) was not a sequence.");
        if (!fast_desired_shares_ids)
            goto err;
        num_desired_shares = PySequence_Fast_GET_SIZE(fast_desired_shares_ids);
        fast_desired_shares_ids_items = PySequence_Fast_ITEMS(fast_desired_shares_ids);
        for (i=0; i<num_desired_shares; i++) {
            if (!PyInt_Check(fast_desired_shares_ids_items[i])) {
                py_raise_fec_error("Precondition violation: second argument is required to contain int.");
                goto err;
            }
            c_desired_shares_ids[i] = PyInt_AsLong(fast_desired_shares_ids_items[i]);
            if (c_desired_shares_ids[i] >= self->kk)
                num_check_shares_produced++;
        }
    } else {
        num_desired_shares = self->mm;
        for (i=0; i<num_desired_shares; i++)
            c_desired_shares_ids[i] = i;
        num_check_shares_produced = self->mm - self->kk;
    }

    fastinshares = PySequence_Fast(inshares, "First argument was not a sequence.");
    if (!fastinshares)
        goto err;

    if (PySequence_Fast_GET_SIZE(fastinshares) != self->kk) {
        py_raise_fec_error("Precondition violation: Wrong length -- first argument is required to contain exactly k shares.  len(first): %d, k: %d", PySequence_Fast_GET_SIZE(fastinshares), self->kk); 
        goto err;
    }

    /* Construct a C array of gf*'s of the input data. */
    PyObject** fastinsharesitems = PySequence_Fast_ITEMS(fastinshares);
    if (!fastinsharesitems)
        goto err;
    Py_ssize_t sz, oldsz = 0;
    for (i=0; i<self->kk; i++) {
        if (!PyObject_CheckReadBuffer(fastinsharesitems[i])) {
            py_raise_fec_error("Precondition violation: %u'th item is required to offer the single-segment read character buffer protocol, but it does not.\n", i);
            goto err;
        }
        if (PyObject_AsReadBuffer(fastinsharesitems[i], (const void**)&(incshares[i]), &sz))
            goto err;
        if (oldsz != 0 && oldsz != sz) {
            py_raise_fec_error("Precondition violation: Input shares are required to be all the same length.  oldsz: %Zu, sz: %Zu\n", oldsz, sz);
            goto err;
        }
        oldsz = sz;
    }
    
    /* Allocate space for all of the check shares. */
    unsigned check_share_index = 0; /* index into the check_shares_produced and (parallel) pystrs_produced arrays */
    for (i=0; i<num_desired_shares; i++) {
        if (c_desired_shares_ids[i] >= self->kk) {
            c_desired_checkshares_ids[check_share_index] = c_desired_shares_ids[i];
            pystrs_produced[check_share_index] = PyString_FromStringAndSize(NULL, sz);
            if (pystrs_produced[check_share_index] == NULL)
                goto err;
            check_shares_produced[check_share_index] = (gf*)PyString_AsString(pystrs_produced[check_share_index]);
            if (check_shares_produced[check_share_index] == NULL)
                goto err;
            check_share_index++;
        }
    }
    assert (check_share_index == num_check_shares_produced);

    /* Encode any check shares that are needed. */
    fec_encode(self->fec_matrix, incshares, check_shares_produced, c_desired_checkshares_ids, num_check_shares_produced, sz);

    /* Wrap all requested shares up into a Python list of Python strings. */
    result = PyList_New(num_desired_shares);
    if (result == NULL)
        goto err;
    check_share_index = 0;
    for (i=0; i<num_desired_shares; i++) {
        if (c_desired_shares_ids[i] < self->kk) {
            Py_INCREF(fastinsharesitems[c_desired_shares_ids[i]]);
            if (PyList_SetItem(result, i, fastinsharesitems[c_desired_shares_ids[i]]) == -1) {
                Py_DECREF(fastinsharesitems[c_desired_shares_ids[i]]);
                goto err;
            }
        } else {
            if (PyList_SetItem(result, i, pystrs_produced[check_share_index]) == -1)
                goto err;
            pystrs_produced[check_share_index] = NULL;
            check_share_index++;
        }
    }

    goto cleanup;
  err:
    for (i=0; i<num_check_shares_produced; i++)
        Py_XDECREF(pystrs_produced[i]);
    Py_XDECREF(result); result = NULL;
  cleanup:
    Py_XDECREF(fastinshares); fastinshares=NULL;
    Py_XDECREF(fast_desired_shares_ids); fast_desired_shares_ids=NULL;
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
    {"k", T_INT, offsetof(Encoder, kk), READONLY, "k"},
    {"m", T_INT, offsetof(Encoder, mm), READONLY, "m"},
    {NULL} /* Sentinel */
};

static PyTypeObject Encoder_type = {
    PyObject_HEAD_INIT(NULL)
    0,                         /*ob_size*/
    "fec.Encoder", /*tp_name*/
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
    int kk;
    int mm;

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

    if (!PyArg_ParseTupleAndKeywords(args, kwdict, "ii", kwlist, &self->kk, &self->mm))
        return -1;

    if (self->kk < 1) {
        py_raise_fec_error("Precondition violation: first argument is required to be greater than or equal to 1, but it was %d", self->kk);
	return -1;
    }
    if (self->mm < 1) {
        py_raise_fec_error("Precondition violation: second argument is required to be greater than or equal to 1, but it was %d", self->mm);
	return -1;
    }
    if (self->mm > 256) {
        py_raise_fec_error("Precondition violation: second argument is required to be less than or equal to 256, but it was %d", self->mm);
	return -1;
    }
    if (self->kk > self->mm) {
        py_raise_fec_error("Precondition violation: first argument is required to be less than or equal to the second argument, but they were %d and %d respectively", self->kk, self->mm);
	return -1;
    }
    self->fec_matrix = fec_new(self->kk, self->mm);

    return 0;
}

#define SWAP(a,b,t) {t tmp; tmp=a; a=b; b=tmp;}

static char Decoder_decode__doc__[] = "\
Decode a list shares into a list of segments.\n\
@param shares a sequence of buffers containing share data (for best performance, make it a tuple instead of a list)\n\
@param shareids a sequence of integers of the shareid for each share in shares (for best performance, make it a tuple instead of a list)\n\
\n\
@return a list of strings containing the segment data (i.e. ''.join(retval) yields a string containing the decoded data)\n\
";

static PyObject *
Decoder_decode(Decoder *self, PyObject *args) {
    PyObject*restrict shares;
    PyObject*restrict shareids;
    PyObject* result = NULL;

    if (!PyArg_ParseTuple(args, "OO", &shares, &shareids))
        return NULL;

    const gf*restrict cshares[self->kk];
    unsigned cshareids[self->kk];
    gf*restrict recoveredcstrs[self->kk]; /* self->kk is actually an upper bound -- we probably won't need all of this space. */
    PyObject*restrict recoveredpystrs[self->kk]; /* self->kk is actually an upper bound -- we probably won't need all of this space. */
    unsigned i;
    for (i=0; i<self->kk; i++)
        recoveredpystrs[i] = NULL;
    PyObject*restrict fastshareids = NULL;
    PyObject*restrict fastshares = PySequence_Fast(shares, "First argument was not a sequence.");
    if (!fastshares)
        goto err;
    fastshareids = PySequence_Fast(shareids, "Second argument was not a sequence.");
    if (!fastshareids)
        goto err;

    if (PySequence_Fast_GET_SIZE(fastshares) != self->kk) {
        py_raise_fec_error("Precondition violation: Wrong length -- first argument is required to contain exactly k shares.  len(first): %d, k: %d", PySequence_Fast_GET_SIZE(fastshares), self->kk); 
        goto err;
    }
    if (PySequence_Fast_GET_SIZE(fastshareids) != self->kk) {
        py_raise_fec_error("Precondition violation: Wrong length -- shareids is required to contain exactly k shares.  len(shareids): %d, k: %d", PySequence_Fast_GET_SIZE(fastshareids), self->kk); 
        goto err;
    }

    /* Construct a C array of gf*'s of the data and another of C ints of the shareids. */
    unsigned needtorecover=0;
    PyObject** fastshareidsitems = PySequence_Fast_ITEMS(fastshareids);
    if (!fastshareidsitems)
        goto err;
    PyObject** fastsharesitems = PySequence_Fast_ITEMS(fastshares);
    if (!fastsharesitems)
        goto err;
    Py_ssize_t sz, oldsz = 0;
    for (i=0; i<self->kk; i++) {
        if (!PyInt_Check(fastshareidsitems[i])) {
            py_raise_fec_error("Precondition violation: second argument is required to contain int.");
            goto err;
        }
        long tmpl = PyInt_AsLong(fastshareidsitems[i]);
        if (tmpl < 0 || tmpl > 255) {
            py_raise_fec_error("Precondition violation: Share ids can't be less than zero or greater than 255.  %ld\n", tmpl);
            goto err;
        }
        cshareids[i] = (unsigned)tmpl;
        if (cshareids[i] >= self->kk)
            needtorecover+=1;

        if (!PyObject_CheckReadBuffer(fastsharesitems[i])) {
            py_raise_fec_error("Precondition violation: %u'th item is required to offer the single-segment read character buffer protocol, but it does not.\n", i);
            goto err;
        }
        if (PyObject_AsReadBuffer(fastsharesitems[i], (const void**)&(cshares[i]), &sz))
            goto err;
        if (oldsz != 0 && oldsz != sz) {
            py_raise_fec_error("Precondition violation: Input shares are required to be all the same length.  oldsz: %Zu, sz: %Zu\n", oldsz, sz);
            goto err;
        }
        oldsz = sz;
    }

    /* move src packets into position */
    for (i=0; i<self->kk;) {
        if (cshareids[i] >= self->kk || cshareids[i] == i)
            i++;
        else {
            /* put pkt in the right position. */
            unsigned c = cshareids[i];

            SWAP (cshareids[i], cshareids[c], int);
            SWAP (cshares[i], cshares[c], const gf*);
            SWAP (fastsharesitems[i], fastsharesitems[c], PyObject*);
        }
    }

    /* Allocate space for all of the recovered shares. */
    for (i=0; i<needtorecover; i++) {
        recoveredpystrs[i] = PyString_FromStringAndSize(NULL, sz);
        if (recoveredpystrs[i] == NULL)
            goto err;
        recoveredcstrs[i] = (gf*)PyString_AsString(recoveredpystrs[i]);
        if (recoveredcstrs[i] == NULL)
            goto err;
    }

    /* Decode any recovered shares that are needed. */
    fec_decode(self->fec_matrix, cshares, recoveredcstrs, cshareids, sz);

    /* Wrap up both original primary shares and decoded shares into a Python list of Python strings. */
    unsigned nextrecoveredix=0;
    result = PyList_New(self->kk);
    if (result == NULL)
        goto err;
    for (i=0; i<self->kk; i++) {
        if (cshareids[i] == i) {
            /* Original primary share. */
            Py_INCREF(fastsharesitems[i]);
            if (PyList_SetItem(result, i, fastsharesitems[i]) == -1) {
                Py_DECREF(fastsharesitems[i]);
                goto err;
            }
        } else {
            /* Recovered share. */
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
    Py_XDECREF(fastshares); fastshares=NULL;
    Py_XDECREF(fastshareids); fastshareids=NULL;
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
    {"k", T_INT, offsetof(Encoder, kk), READONLY, "k"},
    {"m", T_INT, offsetof(Encoder, mm), READONLY, "m"},
    {NULL} /* Sentinel */
};

static PyTypeObject Decoder_type = {
    PyObject_HEAD_INIT(NULL)
    0,                         /*ob_size*/
    "fec.Decoder", /*tp_name*/
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

