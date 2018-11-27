
import ROOT
from pyspark import SparkConf, SparkContext, SparkFiles

DEBUG = False

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
    ranges = BuildRanges(nfiles, npartitions, filelist, treename)

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
      import ROOT

      ROOT.TH1.AddDirectory(False)

      start = int(rg.start)
      end = int(rg.end)

      #return [(start,end)]

      chain = ROOT.TChain("TotemNtuple")
      for f in rg.filelist:
        chain.Add(f)
      # We assume 'end' is exclusive
      chain.SetCacheEntryRange(start, end)

      tdf = ROOT.ROOT.RDataFrame(chain)
      tdf_r = tdf.Range(start, end)

      if mapperIsCpp:
        ROOT.gInterpreter.Declare(mapperCode)
        res =  ROOT.__getattr__(mapperName)(tdf_r)
      else:
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
      #return x + y
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

def GetClusters(filelist, treename):
  import ROOT

  clusters = []
  offset = 0
  for filename in filelist:
    f = ROOT.TFile.Open(filename)
    t = f.Get(treename)

    entries = t.GetEntriesFast()
    it = t.GetClusterIterator(0)
    start = it()
    end = 0

    while start < entries:
      end = it()
      clusters.append((start + offset, end + offset, offset, filename))
      start = end

    offset += entries

  return clusters

def BuildRanges(nfiles, npartitions, filelist, treename):
  clusters = GetClusters(filelist, treename)
  numclusters = len(clusters)
  partSize = numclusters / npartitions
  remainder = numclusters % npartitions

  if DEBUG:
    print("Num clusters", numclusters)
    print("Partsize", partSize)
    print("Remainder", remainder)
    print("Initial clusters", clusters)

  i = 0
  ranges = []
  entries_to_process = 0
  while i < numclusters:
    index_start = i
    start = clusters[i][0]
    i = i + partSize
    if remainder > 0:
      i += 1
      remainder -= 1
    index_end = i
    if i == numclusters:
      end = clusters[-1][1]
    else:
      end = clusters[i-1][1]

    range_files = []
    for idx in range(index_start, index_end):
      current_file = clusters[idx][3]
      if range_files and range_files[-1] == current_file:
        continue
      range_files.append(clusters[idx][3])

    offset_first_cluster = clusters[index_start][2]
    ranges.append(Range(start - offset_first_cluster, end - offset_first_cluster, range_files))
    entries_to_process += (end - start)

  if DEBUG:
    print("Entries to process", entries_to_process)
    print("Final ranges", ranges)

  return ranges

class Range(object):
  def __init__(self, start, end, filelist):
    self.start = start
    self.end = end
    self.filelist = filelist

  def __repr__(self):
    return "(" + str(self.start) + "," + str(self.end) + "), " + str(self.filelist)


