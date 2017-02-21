
import ROOT
from pyspark import SparkConf, SparkContext, SparkFiles


######################################################################
# Function to initialize the Spark context. We need to configure:    #
# 1. The URL of the Spark master                                     #
# 2. The PATH, LD_LIBRARY_PATH and PYTHONPATH for ROOT in the server #
######################################################################

def InitSpark():
  import os

  conf = SparkConf()
  conf.setAppName("ROOT")
  conf.set('spark.executor.extraLibraryPath', os.environ['LD_LIBRARY_PATH'])
  conf.set('spark.executorEnv.KRB5CCNAME', os.environ['KRB5CCNAME'])
  conf.set('spark.yarn.dist.files', os.environ['KRB5CCNAME'] + '#krbcache')
  #conf.set('spark.executor.memory', '1g')
  #conf.set('spark.driver.memory', '2g')
  #conf.set('spark.driver.maxResultSize', '1g')

  #conf.set('spark.driver.port', '9000')
  #conf.set('spark.fileserver.port', '9001')
  #conf.set('spark.blockManager.port', '9002') 

  sc = SparkContext(conf = conf)
  return sc


######################################################################
# Function to initialize user credentials on the Spark executors.    #
######################################################################

def InitUserCredentials(sc):
  def configMap(_):
    import os
    return os.system("ln -sf " + SparkFiles.get('krbcache') + " " + os.environ["KRB5CCNAME"].split(":")[1])

  results = sc.parallelize(range(sc.defaultParallelism)).map(configMap).collect()
  for r in results:
    if r != 0:
      print "Error initializing user credentials on Spark cluster"
      exit(1)


######################################################################
# Utilities to obtain the code of a function given its name          #
######################################################################

ROOT.gInterpreter.Declare('''
std::string getFunctionBodyFromName(const char* fname)
{
   auto v = gInterpreter->CreateTemporary();
   gInterpreter->Evaluate(fname,*v);
   if (!v->IsValid()) return "";
   return v->ToString();
}
''')

def GetFunctionCode(fname):
  import string
  code = ROOT.getFunctionBodyFromName(fname)
  return string.join(code.split('\n')[2:-2], '\n')

def GetFunctionHash(fcode):
  import hashlib
  return hashlib.md5(fcode).hexdigest()

def GetWrappedFunctionCode(fname):
  code = GetFunctionCode(fname)
  code = GetFunctionCode(fname)
  codeHash = GetFunctionHash(code)
  return """
#ifndef __DistROOT__{codeHash}
#define __DistROOT__{codeHash}
{functionCode}
#endif
""".format(codeHash = codeHash, functionCode = code)


################################################################
# Class to create distributed trees.                           #
# A distributed tree encapsulates a Spark RDD of entry ranges. #
################################################################

class DistTree(object):

  def __init__(self, filelist, treename, npartitions):
    # Get number of entries and build the ranges according to npartitions
    chain = ROOT.TChain(treename)
    for fname in filelist:
      chain.Add(fname)
    nentries = chain.GetEntries()
    ranges = BuildRanges(nentries, npartitions, chain)

    # Initialize Spark context
    sc = InitSpark()
   
    # Parallelize the ranges with Spark
    self.ranges = sc.parallelize(ranges, npartitions)

  def ProcessAndMerge(self, fMap, fReduce):
    # Check if mapper and reducer functions are Python or C++
    mapperIsCpp  = isinstance(fMap, ROOT.MethodProxy)
    reducerIsCpp = isinstance(fReduce, ROOT.MethodProxy)
    mapperName = reducerName = mapperCode = reducerCode = None
   
    if mapperIsCpp:
      mapperName  = fMap.func_code.co_name
      mapperCode  = GetWrappedFunctionCode(mapperName)
      # Add defines in case tasks run locally in this very same process
      ROOT.gInterpreter.Declare("""
#ifndef __DistROOT__{mapHash}
#define __DistROOT__{mapHash}
#endif
""".format(mapHash = GetFunctionHash(GetFunctionCode(mapperName))))

    if reducerIsCpp:
      reducerName = fReduce.func_code.co_name
      reducerCode = GetWrappedFunctionCode(reducerName)
      # Add defines in case tasks run locally in this very same process
      ROOT.gInterpreter.Declare("""
#ifndef __DistROOT__{reduceHash}
#define __DistROOT__{reduceHash}
#endif
""".format(reduceHash = GetFunctionHash(GetFunctionCode(reducerName))))

    def mapWrapper(rg):
      import os
      os.system("ln -sf " + SparkFiles.get('krbcache') + " " + os.environ["KRB5CCNAME"].split(":")[1])

      import ROOT
      ROOT.TH1.AddDirectory(False)
      reader = ROOT.TTreeReader(rg.chain)
      reader.SetEntriesRange(rg.start, rg.end)

      if mapperIsCpp:
        ROOT.gInterpreter.Declare(mapperCode)
        res = ROOT.__getattr__(mapperName)(reader)
      else:
        res = fMap(reader)

      return res

    def reduceWrapper(x, y):
      if reducerIsCpp:
        import ROOT
        ROOT.gInterpreter.Declare(reducerCode)
        return ROOT.__getattr__(reducerName)(x, y)
      else:
        return fReduce(x, y) 
      
    return self.ranges.map(mapWrapper).treeReduce(reduceWrapper)
 
  def GetPartitions(self):
    return self.ranges.collect()


####################################################################
# Function and class to create ranges.                             #
# A range represents a logical partition of the entries of a chain #
# and is the basis for parallelization.                            #
####################################################################

def BuildRanges(nentries, npartitions, chain):
  i = 0
  ranges = []
  partSize = nentries / npartitions
    
  while i < nentries:
    start = i
    i = i + partSize
    if i > nentries:
      end = nentries
    else:
      end = i
    ranges.append(Range(start, end, chain))

  return ranges


class Range(object):
  def __init__(self, start, end, chain):
    self.start = start
    self.end = end
    self.chain = chain

  def __repr__(self):
    return "(" + str(self.start) + "," + str(self.end - 1) + ")"
