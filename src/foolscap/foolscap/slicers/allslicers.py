
######################## Slicers+Unslicers

# note that Slicing is always easier than Unslicing, because Unslicing
# is the side where you are dealing with the danger

from foolscap.slicers.none import NoneSlicer, NoneUnslicer
from foolscap.slicers.bool import BooleanSlicer, BooleanUnslicer
from foolscap.slicers.unicode import UnicodeSlicer, UnicodeUnslicer
from foolscap.slicers.list import ListSlicer, ListUnslicer
from foolscap.slicers.tuple import TupleSlicer, TupleUnslicer
from foolscap.slicers.set import SetSlicer, SetUnslicer
from foolscap.slicers.set import FrozenSetSlicer, FrozenSetUnslicer
#from foolscap.slicers.set import BuiltinSetSlicer
from foolscap.slicers.dict import DictSlicer, DictUnslicer, OrderedDictSlicer
from foolscap.slicers.vocab import ReplaceVocabSlicer, ReplaceVocabUnslicer
from foolscap.slicers.vocab import ReplaceVocabularyTable, AddToVocabularyTable
from foolscap.slicers.vocab import AddVocabSlicer, AddVocabUnslicer
from foolscap.slicers.root import RootSlicer, RootUnslicer

# appease pyflakes
unused = [
    NoneSlicer, NoneUnslicer,
    BooleanSlicer, BooleanUnslicer,
    UnicodeSlicer, UnicodeUnslicer,
    ListSlicer, ListUnslicer,
    TupleSlicer, TupleUnslicer,
    SetSlicer, SetUnslicer,
    FrozenSetSlicer, FrozenSetUnslicer,
    #from foolscap.slicers.set import BuiltinSetSlicer
    DictSlicer, DictUnslicer, OrderedDictSlicer,
    ReplaceVocabSlicer, ReplaceVocabUnslicer,
    ReplaceVocabularyTable, AddToVocabularyTable,
    AddVocabSlicer, AddVocabUnslicer,
    RootSlicer, RootUnslicer,
    ]
