# PyROOT Parallelization with Spark
Project that explores the [Spark](http://spark.apache.org/) parallelization of [ROOT](https://root.cern/) analysis, in particular using the ROOT Python interface (PyROOT).

The parallelisation strategy applies the map-reduce pattern to the processing of a ROOT [TTree](https://root.cern.ch/doc/v608/classTTree.html). In the map phase, each mapper reads and processes a sub-range of TTree entries and produces a partial result, while the reduce phase combines all the partial outputs into a final result (i.e. a set of filled histograms).

In the programming model, based on the Python language, the user creates a `DistTree` object from a list of files containing a TTree and the TTree name. Moreover, the number of partitions (sub-ranges) of the TTree can also be specified. In order to start the parallel processing, the user invokes the `ProcessAndMerge` function on the `DistTree`. The parameters of this function are the mapper and reducer functions. The mapper receives a [TTreeReader](https://root.cern.ch/doc/v608/classTTreeReader.html), a ROOT object that represents a sub-range of entries and that can be iterated on.

This code snippet gives an example of how the `DistTree` class can be used:
```python
# ROOT imports
import ROOT
from DistROOT import DistTree

# Build the DistTree
dTree = DistTree(filelist = ["myFile1", "myFile2"],
                 treename = "myTree",
                 npartitions = 8)

# Trigger the parallel processing
myHistos = dTree.ProcessAndMerge(fillHistos, mergeHistos)
```

Authors:
* [Enric Tejedor](mailto:etejedor@cern.ch)
* [Danilo Piparo](mailto:danilo.piparo@cern.ch)
