# root-spark
Project that explores the Spark parallelization of ROOT analysis.

The parallelisation strategy applies the map-reduce pattern to the processing of a ROOT TTree. In the map phase, each mapper reads and processes a sub-range of TTree entries and produces a partial result, while the reduce phase combines all the partial outputs into a final result (i.e. a set of filled histograms).
