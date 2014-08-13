#!/usr/bin/env python
# ENCODE_BWA 0.0.1

import os
import dxpy

import subprocess, shlex
from multiprocessing import Pool, cpu_count
import pdb

def run_pipe(steps, outfile=None):
    #break this out into a recursive function
    from subprocess import Popen, PIPE
    p = None
    p_next = None
    first_step_n = 1
    last_step_n = len(steps)
    for n,step in enumerate(steps, start=first_step_n):
        if n == first_step_n:
            p = Popen(shlex.split(step), stdout=PIPE)
        elif n == last_step_n and outfile: #only treat the last step specially if you're sending stdout to a file
            with open(outfile, 'w') as fh:
                p_last = Popen(shlex.split(step), stdin=p.stdout, stdout=fh)
                p.stdout.close()
                p = p_last
        else: #handles intermediate steps and, in the case of a pipe to stdout, the last step
            p_next = Popen(shlex.split(step), stdin=p.stdout, stdout=PIPE)
            p.stdout.close()
            p = p_next
    out,err = p.communicate()
    return out,err

@dxpy.entry_point("postprocess")
def postprocess(indexed_reads, unmapped_reads, reference_tar):

    print "In postprocess with:"

    indexed_reads_filenames = []
    unmapped_reads_filenames = []
    for i,reads in enumerate(indexed_reads):
        read_pair_number = i+1
        
        fn = dxpy.describe(reads)['name']
        print "indexed_reads %d: %s" %(read_pair_number, fn)
        indexed_reads_filenames.append(fn)
        dxpy.download_dxfile(reads,fn)

        unmapped = unmapped_reads[i]
        fn = dxpy.describe(unmapped)['name']
        print "unmapped reads %d: %s" %(read_pair_number, fn)
        unmapped_reads_filenames.append(fn)
        dxpy.download_dxfile(unmapped,fn)

    reference_tar_filename = dxpy.describe(reference_tar)['name']
    print "reference_tar: %s" %(reference_tar_filename)
    dxpy.download_dxfile(reference_tar, reference_tar_filename)
    # extract the reference files from the tar
    tar_command = 'tar -xvf %s' %(reference_tar_filename)
    print subprocess.check_output(shlex.split(tar_command))
    # assume the reference file is the only .fa file
    reference_filename = subprocess.check_output('ls *.fa', shell=True).rstrip()

    paired_end = len(indexed_reads) == 2

    if paired_end:
        r1_basename = unmapped_reads_filenames[0].rstrip('.gz').rstrip('.fq').rstrip('.fastq')
        r2_basename = unmapped_reads_filenames[1].rstrip('.gz').rstrip('.fq').rstrip('.fastq')
        reads_basename = r1_basename + r2_basename
    else:
        reads_basename = unmapped_reads_filenames[0].rstrip('.gz').rstrip('.fq').rstrip('.fastq')
    raw_bam_filename = '%s.raw.srt.bam' %(reads_basename)
    raw_bam_mapstats_filename = '%s.raw.srt.bam.flagstat.qc' %(reads_basename)

    if paired_end:
        reads1_filename = indexed_reads_filenames[0]
        reads2_filename = indexed_reads_filenames[1]
        unmapped_reads1_filename = unmapped_reads_filenames[0]
        unmapped_reads2_filename = unmapped_reads_filenames[1]
        raw_sam_filename = reads_basename + ".raw.sam"
        badcigar_filename = "badreads.tmp"
        steps = [ "bwa sampe %s %s %s %s %s" %(reference_filename, reads1_filename, reads2_filename, unmapped_reads1_filename, unmapped_reads2_filename),
                  "tee %s" %(raw_sam_filename),
                  r"""awk 'BEGIN {FS="\t" ; OFS="\t"} ! /^@/ && $6!="*" { cigar=$6; gsub("[0-9]+D","",cigar); n = split(cigar,vals,"[A-Z]"); s = 0; for (i=1;i<=n;i++) s=s+vals[i]; seqlen=length($10) ; if (s!=seqlen) print $1 ; }'""",
                  "sort",
                  "uniq" ]
        out,err = run_pipe(steps,badcigar_filename)
        if err:
            print "sampe error: %s" %(err)
        steps = [ "cat %s" %(raw_sam_filename),
                  "grep -v -F -f %s" %(badcigar_filename),
                  "samtools view -Su -",
                  "samtools sort - %s" %(raw_bam_filename).rstrip('.bam') ] # samtools adds .bam
        out,err = run_pipe(steps)
    else: #single end
        reads_filename = indexed_reads_filenames[0]
        unmapped_reads_filename = unmapped_reads_filenames[0]
        steps = [ "bwa samse %s %s %s" %(reference_filename, reads_filename, unmapped_reads_filename),
                  "samtools view -Su -",
                  "samtools sort - %s" %(raw_bam_filename).rstrip('.bam') ] # samtools adds .bam
        out,err = run_pipe(steps)

    if out:
        print "samtools output: %s" %(out)
    if err:
        print "samtools error: %s" %(err)

    with open(raw_bam_mapstats_filename, 'w') as fh:
        subprocess.check_call(shlex.split("samtools flagstat %s" \
            %(raw_bam_filename)), stdout=fh)

    print subprocess.check_output('ls', shell=True)
    mapped_reads = dxpy.upload_local_file(raw_bam_filename)
    mapping_statistics = dxpy.upload_local_file(raw_bam_mapstats_filename)

    output = { "mapped_reads": dxpy.dxlink(mapped_reads),
               "mapping_statistics": dxpy.dxlink(mapping_statistics) }
    print "Returning from post with output: %s" %(output)
    return output

@dxpy.entry_point("process")
def process(reads_file, reference_tar, bwa_aln_params):
    # reads_file, reference_tar should be links to file objects.
    # reference_tar should be a tar of files generated by bwa index and
    # the tar should be uncompressed to avoid repeating the decompression.

    print "In process"

    # Generate filename strings and download the files to the local filesystem
    reads_filename = dxpy.describe(reads_file)['name']
    reads_basename = reads_filename.rstrip('.gz').rstrip('.fq').rstrip('.fastq')
    reads_file = dxpy.download_dxfile(reads_file,reads_filename)

    reference_tar_filename = dxpy.describe(reference_tar)['name']
    reference_tar_file = dxpy.download_dxfile(reference_tar,reference_tar_filename)
    # extract the reference files from the tar
    tar_command = 'tar -xvf %s' %(reference_tar_filename)
    print subprocess.check_output(shlex.split(tar_command))
    # assume the reference file is the only .fa file
    reference_filename = subprocess.check_output('ls *.fa', shell=True).rstrip()

    print subprocess.check_output('ls -l', shell=True)

    #generate the suffix array index file
    sai_filename = '%s.sai' %(reads_basename)
    with open(sai_filename,'w') as sai_file:
        # Build the bwa command and call bwa
        bwa_command = "bwa aln %s -t %d %s %s" \
            %(bwa_aln_params, cpu_count(), reference_filename, reads_filename)
        print bwa_command
        subprocess.check_call(shlex.split(bwa_command), stdout=sai_file) 

    print subprocess.check_output('ls -l', shell=True)

    # Upload the output to the DNAnexus project
    print "Uploading %s" %(sai_filename)
    sai_dxfile = dxpy.upload_local_file(sai_filename)
    process_output = { "output": dxpy.dxlink(sai_dxfile) }
    print "Returning from process:"
    print process_output
    return process_output

@dxpy.entry_point("main")
def main(reads1, reference_tar, bwa_aln_params, reads2=None):

    # Main entry-point.  Parameter defaults assumed to come from dxapp.json.
    # reads1, reference_tar, reads2 are links to DNAnexus files or None

    # This spawns only one or two subjobs for single- or paired-end,
    # respectively.  It could also download the files, chunk the reads,
    # and spawn multiple subjobs.

    # Files are downloaded later by subjobs into their own filesystems
    # and uploaded to the project.

    # Initialize file handlers for input files.

    paired_end = reads2 is not None
    unmapped_reads = [r for r in [reads1, reads2] if r]
    
    subjobs = []
    for reads in unmapped_reads:
        subjob_input = {"reads_file": reads, "reference_tar": reference_tar, "bwa_aln_params": bwa_aln_params}
        print "Submitting:"
        print subjob_input
        subjobs.append(dxpy.new_dxjob(subjob_input, "process"))

    # Create the job that will perform the "postprocess" step.  depends_on=subjobs, so blocks on all subjobs

    postprocess_job = dxpy.new_dxjob(fn_input={ "indexed_reads": [subjob.get_output_ref("output") for subjob in subjobs],
                                                "unmapped_reads": unmapped_reads,
                                                "reference_tar": reference_tar },
                                     fn_name="postprocess",
                                     depends_on=subjobs)

    mapped_reads = postprocess_job.get_output_ref("mapped_reads")
    mapping_statistics = postprocess_job.get_output_ref("mapping_statistics")

    output = { "mapped_reads": mapped_reads, "mapping_statistics": mapping_statistics, "paired_end": paired_end }
    print "Exiting with output: %s" %(output)
    return output

dxpy.run()