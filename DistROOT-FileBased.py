
import ROOT
from pyspark import SparkConf, SparkContext, SparkFiles


######################################################################
# Function to initialize the Spark context. We need to configure:    #
# 1. The URL of the Spark master                                     #
# 2. The PATH, LD_LIBRARY_PATH and PYTHONPATH for ROOT in the server #
######################################################################

def InitSpark():
  return SparkContext.getOrCreate()


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
    nfiles = len(filelist)
    ranges = BuildRanges(nfiles, npartitions, filelist)

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
https://swan006.cern.ch/user/etejedor/edit/SWAN_projects/SWAN_Spark/Spark-Notebooks/DistROOT.py#""".format(mapHash = GetFunctionHash(GetFunctionCode(mapperName))))

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
      import ROOT
      ROOT.TH1.AddDirectory(False)
      #reader = ROOT.TTreeReader(rg.chain)
      #reader.SetEntriesRange(rg.start, rg.end)
      #tdf = ROOT.Experimental.TDataFrame(rg.chain)
    
      vec_input_files = ROOT.vector('string')()
      [vec_input_files.push_back(f) for f in rg.filelist]
    
      tdf_r = ROOT.ROOT.RDataFrame("TotemNtuple", vec_input_files)

      if mapperIsCpp:
        ROOT.gInterpreter.Declare(mapperCode)
        #res = ROOT.__getattr__(mapperName)(reader)
        res =  ROOT.__getattr__(mapperName)(tdf_r)
      else:
        #res = fMap(reader)
        res = fMap(tdf_r)
        
      ## SHARED PTR TO PROXIED OBJECT, DOES NOT WORK
      ## Caused by: java.io.EOFException
      ## at java.io.DataInputStream.readInt(DataInputStream.java:392)
      #ref = res.GetValue()
      #h = ROOT.std.shared_ptr("TH1D")()
      #h.reset(ROOT.AddressOf(ref))
      #return h
    
      # Quick hack with extra copies and just checking for TH1F and TH2F return values
      if isinstance(res, list):
        return [ ROOT.TH1D(h.GetValue()) if isinstance(h.GetValue(), ROOT.TH1D) else ROOT.TH2D(h.GetValue()) for h in res ]
      else:
        if isinstance(res.GetValue(), ROOT.TH1D): return ROOT.TH1D(res.GetValue())
        else:                                     return ROOT.TH2D(res.GetValue())
    
      ## Trigger the event loop, then return the proxy
      ## DOES NOT WORK:
      ## Caused by: java.io.EOFException
      ## at java.io.DataInputStream.readInt(DataInputStream.java:392)
      #if isinstance(res, list):
      #  if res: res[0].GetValue()
      #else:
      #  res.GetValue()
      #return res
      
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

def BuildRanges(nfiles, npartitions, filelist):
  i = 0
  ranges = []
  partSize = nfiles / npartitions
  remainder = nfiles % npartitions
  
  #print("Nentries", nentries)
  #print("Partsize", partSize)
  #print("Remainder", remainder)
  while i < nfiles:
    start = i
    singleRange = Range()
    remainderPerPart = 0
    if remainder > 0:
        remainderPerPart += 1
        remainder -= 1
    while i < start + partSize + remainderPerPart:
      singleRange.append(filelist.pop())
      i += 1
    ranges.append(singleRange)
  return ranges


class Range(object):
  def __init__(self):
    self.filelist = []

  def __repr__(self):
    return "(" + str(self.filelist) + ")"

  def append(self, filename):
    self.filelist.append(filename)


